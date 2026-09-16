"""Case definition: the public facts, plus the identifiers each source needs.

TOML rather than YAML so the toolkit has zero third-party dependencies
(tomllib is stdlib on 3.11+).

WHERE CASE FILES LIVE. A real case names a living person and describes them,
so real case files are kept OUTSIDE this repository, in a notes vault. This
repo ships only a synthetic example. Resolution order:

  1. $MP_CASES_DIR, if set
  2. ~/Exobrain/Areas/Community/Missing Persons/cases  (the vault)
  3. ./cases  in this repo  (example only)

Directories are searched in that order and `available()` unions them, so a
private case shadows a same-named example rather than colliding with it.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_CASES = Path(__file__).resolve().parent.parent / "cases"
VAULT_CASES = Path.home() / "Exobrain" / "Areas" / "Community" / "Missing Persons" / "cases"


def case_dirs() -> list[Path]:
    """Every directory searched for case files, highest priority first."""
    dirs: list[Path] = []
    override = os.environ.get("MP_CASES_DIR")
    if override:
        dirs.append(Path(override).expanduser())
    dirs += [VAULT_CASES, REPO_CASES]
    return [d for d in dirs if d.is_dir()]


def resolve(case_id: str) -> Path:
    """Find a case file by id, or say exactly where it was looked for."""
    if case_id.endswith(".toml"):
        return Path(case_id).expanduser()
    searched = case_dirs()
    for directory in searched:
        candidate = directory / f"{case_id}.toml"
        if candidate.is_file():
            return candidate
    where = "\n  ".join(str(d) for d in searched) or "(no case directory exists yet)"
    raise FileNotFoundError(
        f"no case file '{case_id}.toml'. Searched:\n  {where}\n"
        "Set MP_CASES_DIR, or put the case file in the vault directory."
    )


@dataclass(frozen=True)
class Description:
    """Physical description, held as RANGES.

    Ranges, not points, because the sources disagree: the family flyer says
    5'1", KCPD's release to KSHB said 5'2", NamUs carries 62 inches. A search
    that filters on one number silently discards the candidate that the other
    number would have caught, which is the exact failure this tool exists to
    avoid.
    """

    sex: str
    race: str
    height_in: tuple[int, int]
    weight_lb: tuple[int, int]
    hair: str
    eyes: str
    age_at_disappearance: int


@dataclass(frozen=True)
class Location:
    """One claimed last-seen location, with its provenance AND its precision.

    Cases routinely carry more than one. Keeping them side by side with the
    source that asserted each is how the discrepancy stays visible instead of
    being resolved by whichever file was written last.

    `precision_mi` is not decoration. Public databases publish DELIBERATELY
    COARSENED coordinates: NamUs's `publicGeolocation` is a centroid, measured
    on a control case at 748 ft from the town centre for a city-level address.
    Subtracting such a point from a street intersection yields a number that
    looks like a finding and is an artifact. Record how precise each claim
    actually is, and let `geo.separation` refuse to call the difference
    meaningful when it falls inside the combined uncertainty.

    Rules of thumb: street address ~0.05, named intersection ~0.25,
    neighbourhood ~1.0, ZIP or city centroid ~2.5.
    """

    label: str
    source: str
    lat: float
    lon: float
    note: str = ""
    precision_mi: float = 0.25
    # Census ZCTA for this point. Worth carrying separately from the
    # coordinate: a coarsened centroid cannot be compared against a street
    # intersection, but the ZIP a source ACTUALLY RECORDED can be compared
    # against the ZIP a location actually falls in, and that comparison has
    # resolution the distance calculation does not.
    zcta: str = ""
    # Whether this point is worth putting boots on. A ZIP or city centroid is
    # a legitimate record of what a source asserted AND a useless search
    # target: mapping terrain around it maps terrain around an arbitrary spot
    # inside a postal area. Defaults to True, and defaults to False for
    # anything coarser than a mile, because that is the honest default.
    searchable: bool = True


@dataclass(frozen=True)
class Document:
    """A source document the case cites, e.g. an agency bulletin PDF.

    Carried as structured data rather than a bare link because a link is not a
    read: this case listed its NCIC bulletin as a URL for a full day while the
    only specific address any source gave sat unread inside it.
    """

    label: str
    url: str
    kind: str = "pdf"
    note: str = ""


@dataclass(frozen=True)
class Case:
    id: str
    display_name: str
    legal_name: str
    aka: list[str]
    description: Description
    last_seen_date: date
    recovery_floor: date
    date_claims: dict[str, str]
    locations: list[Location]
    clothing: str
    agency: str
    agency_phones: dict[str, str]
    namus_mp: int | None
    mshp_first: str
    mshp_last: str
    search_states: list[str]
    news_terms: list[str]
    locality_terms: list[str]
    documents: list[Document] = field(default_factory=list)
    cases_dir: Path = REPO_CASES
    links: dict[str, str] = field(default_factory=dict)

    @property
    def primary_location(self) -> Location:
        return self.locations[0]

    @property
    def search_locations(self) -> list[Location]:
        """Locations precise enough that searching around them means something."""
        return [loc for loc in self.locations if loc.searchable]


def _pair(raw: Any, what: str) -> tuple[int, int]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise ValueError(f"{what} must be a two-element range, got {raw!r}")
    lo, hi = int(raw[0]), int(raw[1])
    return (min(lo, hi), max(lo, hi))


def load(case_id: str) -> Case:
    path = resolve(case_id)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    d = raw["description"]
    desc = Description(
        sex=d["sex"],
        race=d["race"],
        height_in=_pair(d["height_in"], "height_in"),
        weight_lb=_pair(d["weight_lb"], "weight_lb"),
        hair=d["hair"],
        eyes=d["eyes"],
        age_at_disappearance=int(d["age_at_disappearance"]),
    )
    locations = [
        Location(
            label=loc["label"],
            source=loc["source"],
            lat=float(loc["lat"]),
            lon=float(loc["lon"]),
            note=loc.get("note", ""),
            precision_mi=float(loc.get("precision_mi", 0.25)),
            zcta=str(loc.get("zcta", "")),
            searchable=bool(loc.get("searchable", float(loc.get("precision_mi", 0.25)) <= 1.0)),
        )
        for loc in raw["locations"]
    ]
    if not locations:
        raise ValueError(f"case {case_id} has no locations")

    c = raw["case"]
    return Case(
        id=c["id"],
        display_name=c["display_name"],
        legal_name=c["legal_name"],
        aka=list(c.get("aka", [])),
        description=desc,
        last_seen_date=date.fromisoformat(c["last_seen_date"]),
        recovery_floor=date.fromisoformat(
            c.get("recovery_floor") or c["last_seen_date"]),
        date_claims=dict(raw.get("date_claims", {})),
        locations=locations,
        clothing=c.get("clothing", ""),
        agency=c["agency"],
        agency_phones=dict(raw.get("agency_phones", {})),
        namus_mp=c.get("namus_mp"),
        mshp_first=c["mshp_first"],
        mshp_last=c["mshp_last"],
        search_states=list(c.get("search_states", [])),
        news_terms=list(c.get("news_terms", [])),
        locality_terms=list(c.get("locality_terms", [])),
        documents=[
            Document(label=d["label"], url=d["url"], kind=d.get("kind", "pdf"),
                     note=d.get("note", ""))
            for d in raw.get("documents", [])
        ],
        cases_dir=path.parent,
        links=dict(raw.get("links", {})),
    )


def available() -> list[str]:
    """Case ids across every search directory. A private case shadows an example."""
    seen: dict[str, Path] = {}
    for directory in case_dirs():
        for path in sorted(directory.glob("*.toml")):
            seen.setdefault(path.stem, path)
    return sorted(seen)
