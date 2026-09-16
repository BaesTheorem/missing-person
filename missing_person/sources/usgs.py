"""USGS National Hydrography Dataset: authoritative US surface water.

Why this exists alongside Overpass. Water is the highest-value terrain class
for a ground search, and for US water this beats OSM on both authority and
availability: it is the federal dataset the USGS maintains, and it answered
every request during an afternoon when all three public Overpass endpoints
were rate-limiting or timing out.

LAYER CHOICE IS LOAD-BEARING. The service publishes each feature class at two
resolutions, and the small-scale layers are generalised for national-extent
maps. Querying layer 10 ("Waterbody - Small Scale") around a point in suburban
Kansas City returned exactly one feature, a 0.28 km2 lake. The small ponds,
retention basins and creeks that a search party actually walks are only in the
large-scale layers, so this module uses those and nothing else:

    12  Waterbody - Large Scale   lakes, ponds, reservoirs, swamps
     9  Area - Large Scale        wide rivers, braided channels
     6  Flowline - Large Scale    streams, creeks, ditches, canals

A generalised layer does not error when it under-reports. It just answers a
narrower question than the one asked, which is the failure mode this comment
exists to prevent.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from dataclasses import dataclass

from missing_person.net import SourceError, fetch

BASE = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer"
LAYERS = {12: "waterbody", 9: "water area", 6: "flowline"}

# FTYPE/FCODE meanings, read off the service's own renderer rather than from
# memory: the fields carry integer codes and the service publishes no coded
# domain, but its uniqueValue renderer labels every class.
FTYPE = {390: "Lake/Pond", 436: "Reservoir", 466: "Swamp/Marsh", 361: "Playa",
         493: "Estuary", 378: "Ice Mass", 460: "Stream/River", 336: "Canal/Ditch",
         334: "Connector", 558: "Artificial Path", 428: "Pipeline",
         420: "Underground Conduit", 566: "Coastline"}

# Flow permanence, which decides whether a channel holds water year round. It
# changes how a search treats a line on a map, so it is worth carrying.
FCODE_FLOW = {46006: "perennial", 46003: "intermittent", 46007: "ephemeral",
              46000: "perennial"}

# Topological artifacts, not walkable surface water. The NHD threads
# Connectors and Artificial Paths through waterbodies to keep the network
# routable. Dropping them UNNAMED is right; dropping them when NAMED is not,
# because a creek crossing a lake carries its name on the artificial path and
# would otherwise vanish from the list entirely.
TOPOLOGICAL = {334, 558}
SUBSURFACE = {428, 420}

# Per layer, because a layer rejects the ENTIRE query when asked for a field
# it does not have, and flowlines have no areasqkm. That failure surfaces only
# as "Failed to execute query", with no hint which field was wrong.
OUT_FIELDS = {
    12: "gnis_name,ftype,fcode,areasqkm",
     9: "gnis_name,ftype,fcode,areasqkm",
     6: "gnis_name,ftype,fcode",
}


@dataclass(frozen=True)
class Water:
    kind: str
    name: str
    ftype: str
    lat: float
    lon: float
    distance_mi: float

    @property
    def label(self) -> str:
        return self.name or f"(unnamed {self.ftype.lower()})"


def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _vertices(geom: dict) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for key in ("rings", "paths"):
        for part in geom.get(key, []):
            out.extend((pt[1], pt[0]) for pt in part if len(pt) >= 2)
    if "x" in geom and "y" in geom:
        out.append((geom["y"], geom["x"]))
    return out


def _query(layer: int, lat: float, lon: float, radius_mi: float) -> list[dict]:
    params = {
        "geometry": json.dumps({"x": lon, "y": lat,
                                "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "distance": str(int(radius_mi * 1609.34)),
        "units": "esriSRUnit_Meter",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": "4326", "outSR": "4326",
        "outFields": OUT_FIELDS.get(layer, "gnis_name,ftype,fcode"),
        "returnGeometry": "true",
        # Generalise the returned geometry to ~10 m. Enough to measure distance
        # from, and it keeps a braided creek from returning tens of thousands
        # of vertices.
        "maxAllowableOffset": "0.0001",
        "f": "json",
    }
    raw = fetch(f"{BASE}/{layer}/query?{urllib.parse.urlencode(params)}",
                timeout=90, retries=3)
    payload = json.loads(raw)
    if "error" in payload:
        raise SourceError(f"NHD layer {layer}: {payload['error'].get('message')}")
    return payload.get("features", [])


def water_near(lat: float, lon: float, radius_mi: float = 2.0) -> list[Water]:
    """Every NHD water feature within `radius_mi`, nearest first.

    Deduplication has to do two opposite things at once. The NHD splits a
    single creek into many flowline segments, so the same named creek must
    collapse to one entry. But most features here are UNNAMED ponds and
    retention basins, and those are genuinely distinct places. Keying both on
    the display label collapsed 75 separate ponds into one, which is how this
    function first reported 2 features in an area that holds dozens.

    So: named features key on the name, unnamed ones key on rounded position.
    """
    seen: dict[tuple[str, str], Water] = {}
    failures: list[str] = []
    for layer, kind in LAYERS.items():
        try:
            features = _query(layer, lat, lon, radius_mi)
        except SourceError as exc:
            failures.append(str(exc))
            continue
        for feature in features:
            attrs = feature.get("attributes", {})
            verts = _vertices(feature.get("geometry", {}))
            if not verts:
                continue
            nearest = min(verts, key=lambda v: _haversine_mi(lat, lon, v[0], v[1]))
            raw_ftype = attrs.get("ftype", attrs.get("FTYPE"))
            code = int(raw_ftype) if isinstance(raw_ftype, (int, float)) else 0
            name = (attrs.get("gnis_name") or attrs.get("GNIS_NAME") or "").strip()
            if code in SUBSURFACE:
                continue
            if code in TOPOLOGICAL and not name:
                continue
            flow = FCODE_FLOW.get(int(attrs.get("fcode") or 0), "")
            label = FTYPE.get(code, str(raw_ftype))
            if code in TOPOLOGICAL and name:
                label = "Stream/River"
            item = Water(
                kind=kind,
                name=name,
                ftype=f"{label} ({flow})" if flow else label,
                lat=nearest[0], lon=nearest[1],
                distance_mi=round(_haversine_mi(lat, lon, nearest[0], nearest[1]), 2),
            )
            key = ((name.lower(), label) if name
                   else (round(item.lat, 4), round(item.lon, 4), label))
            if key not in seen or item.distance_mi < seen[key].distance_mi:
                seen[key] = item
    if failures and not seen:
        raise SourceError("; ".join(failures))
    return sorted(seen.values(), key=lambda w: w.distance_mi)
