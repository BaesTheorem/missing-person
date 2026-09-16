"""Build a search-area picture around the last-seen point(s) from OpenStreetMap.

What this is for: a volunteer or a family member deciding where to walk, and
what to ask the agency whether it has already covered. Ground search teams
prioritise terrain features over uniform grid sweeps, so this enumerates the
features that dominate outcomes for an adult missing on foot -- water,
drainage, dense cover, rail, and the road network's severance points.

What this is NOT: a prediction of where the person is. It lists terrain.

Overpass note: the main instance returns 504 under load and the kumi /
private.coffee mirrors time out more often than they answer. So queries are
kept small, retried, and tried against the main instance LAST-known-good
first. Expect a slow call, not a failed one.
"""

from __future__ import annotations

import math
import urllib.parse
from dataclasses import dataclass

from missing_person.case import Location
from missing_person.net import SourceError, fetch

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Feature classes that matter for a ground search, in the order a team would
# normally clear them.
FEATURE_QUERIES: dict[str, str] = {
    "open water": '["natural"="water"]',
    "waterway": '["waterway"~"^(river|stream|canal|ditch|drain)$"]',
    "culvert/tunnel": '["tunnel"]["waterway"]',
    "bridge": '["bridge"]["highway"]',
    "woodland": '["natural"="wood"]',
    "scrub": '["natural"="scrub"]',
    "railway": '["railway"~"^(rail|disused|abandoned)$"]',
    "quarry/pit": '["landuse"~"^(quarry|landfill)$"]',
}


@dataclass(frozen=True)
class Feature:
    kind: str
    name: str
    lat: float
    lon: float
    distance_mi: float
    osm: str


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _overpass(query: str, timeout: int = 100) -> dict:
    import json
    last: Exception | None = None
    for endpoint in ENDPOINTS:
        try:
            raw = fetch(
                endpoint,
                data=urllib.parse.urlencode({"data": query}).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=timeout,
                retries=2,
                backoff=5.0,
            )
            return json.loads(raw)
        except (SourceError, ValueError) as exc:
            last = exc
    raise SourceError(f"every Overpass endpoint failed: {last}")


def features_near(origin: Location, radius_mi: float = 2.0,
                  kinds: list[str] | None = None) -> list[Feature]:
    """Terrain features within `radius_mi` of a point, nearest first."""
    radius_m = int(radius_mi * 1609.34)
    wanted = kinds or list(FEATURE_QUERIES)
    out: list[Feature] = []
    for kind in wanted:
        selector = FEATURE_QUERIES[kind]
        query = (
            f"[out:json][timeout:{timeout_for(radius_mi)}];"
            f"(way{selector}(around:{radius_m},{origin.lat},{origin.lon});"
            f" relation{selector}(around:{radius_m},{origin.lat},{origin.lon}););"
            "out tags center;"
        )
        for element in _overpass(query).get("elements", []):
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
            if lat is None or lon is None:
                continue
            tags = element.get("tags", {})
            out.append(Feature(
                kind=kind,
                name=tags.get("name") or tags.get("waterway") or tags.get("natural")
                or tags.get("landuse") or "(unnamed)",
                lat=lat, lon=lon,
                distance_mi=round(haversine_mi(origin.lat, origin.lon, lat, lon), 2),
                osm=f"https://www.openstreetmap.org/{element.get('type')}/{element.get('id')}",
            ))
    out.sort(key=lambda f: f.distance_mi)
    return out


def timeout_for(radius_mi: float) -> int:
    return min(180, int(40 + radius_mi * 25))


def separation(locations: list[Location]) -> list[tuple[str, str, float]]:
    """Pairwise distance between competing last-seen claims.

    When sources disagree about where someone was last seen, the size of the
    disagreement decides whether it can be ignored. Two points 400 feet apart
    are the same place described twice; five miles apart are two hypotheses.
    """
    out: list[tuple[str, str, float]] = []
    for i, a in enumerate(locations):
        for b in locations[i + 1:]:
            out.append((a.label, b.label,
                        round(haversine_mi(a.lat, a.lon, b.lat, b.lon), 2)))
    return out


def map_links(origin: Location, radius_mi: float = 2.0) -> dict[str, str]:
    q = f"{origin.lat},{origin.lon}"
    zoom = 15 if radius_mi <= 2 else 13
    return {
        "OpenStreetMap": f"https://www.openstreetmap.org/#map={zoom}/{origin.lat}/{origin.lon}",
        "Google satellite": f"https://www.google.com/maps/@{origin.lat},{origin.lon},{zoom}z/data=!3m1!1e3",
        "Street View": f"https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(q)}",
    }
