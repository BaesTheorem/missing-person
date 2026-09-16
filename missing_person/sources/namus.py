"""NamUs, the national missing/unidentified/unclaimed persons database.

The public API moved: the old namus.nij.ojp.gov/api/ host 404s now, and the
live one is www.namus.gov/api/. Two things about it shape this module.

1. The search endpoint validates projection field names and reports every bad
   one in the 400 body, which makes it self-documenting. FIELDS below was
   derived that way rather than guessed; see `discover_fields`, kept because
   the field set will drift again.

2. The ONLY predicate operator that works is `IsIn`. IsEqualTo, Contains,
   and every date-range operator tried (GreaterThan, IsOnOrAfter, Between)
   return HTTP 500. So filtering by date has to happen client-side, which is
   fine at these volumes (~15.5k unidentified records nationally, 115 in
   Missouri).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from missing_person.net import get_json, post_json

BASE = "https://www.namus.gov/api/CaseSets/NamUs"
CASE_URL = "https://www.namus.gov/MissingPersons/Case#/{}"
UP_CASE_URL = "https://www.namus.gov/UnidentifiedPersons/Case#/{}"

MP_FIELDS = [
    "idFormatted", "namus2Number", "firstName", "middleName", "lastName",
    "dateOfLastContact", "ethnicities", "heightFrom", "heightTo", "weightFrom",
    "weightTo", "hairColor", "leftEyeColor", "rightEyeColor", "stateOfLastContact",
    "countyOfLastContact", "cityOfLastContact", "modifiedDateTime", "createdDateTime",
    "computedMissingMinAge", "computedMissingMaxAge", "circumstancesOfDisappearance",
]

UP_FIELDS = [
    "idFormatted", "namus2Number", "dateFound", "sex", "ethnicities", "heightFrom",
    "heightTo", "weightFrom", "weightTo", "hairColor", "leftEyeColor", "rightEyeColor",
    "modifiedDateTime", "createdDateTime", "estimatedAgeFrom", "estimatedAgeTo",
    "stateOfRecovery", "countyOfRecovery", "cityOfRecovery", "circumstancesOfRecovery",
]


@dataclass(frozen=True)
class UnidentifiedRecord:
    """One unidentified-person record, normalised into comparable units."""

    case_id: str
    number: int
    date_found: date | None
    sex: str
    ethnicity: str
    height_in: tuple[int, int] | None
    weight_lb: tuple[int, int] | None
    hair: str
    eyes: str
    age_est: tuple[int, int] | None
    state: str
    county: str
    city: str
    circumstances: str
    modified: str

    @property
    def url(self) -> str:
        return UP_CASE_URL.format(self.number)


def _search(case_set: str, fields: list[str], predicates: list[dict[str, Any]],
            take: int = 2000) -> list[dict[str, Any]]:
    """Page through a case set. `take` over ~2000 starts timing out."""
    out: list[dict[str, Any]] = []
    skip = 0
    while True:
        page = post_json(
            f"{BASE}/{case_set}/Search",
            {"take": take, "skip": skip, "projections": fields, "predicates": predicates},
            timeout=90,
        )
        rows = page.get("results", [])
        out.extend(rows)
        total = int(page.get("count", 0))
        skip += len(rows)
        if not rows or skip >= total:
            return out


def discover_fields(case_set: str, candidates: list[str]) -> list[str]:
    """Ask the API which of `candidates` it accepts, using its own 400 body.

    Kept in the shipped module on purpose: when NamUs next renames a field,
    this is how the constant above gets rebuilt in one call instead of by
    bisecting a list of guesses.
    """
    from missing_person.net import SourceError
    try:
        post_json(f"{BASE}/{case_set}/Search",
                  {"take": 1, "skip": 0, "projections": candidates, "predicates": []})
        return list(candidates)
    except SourceError as exc:
        bad = {part.split('"')[1] for part in str(exc).split("Invalid field name ")[1:]
               if '"' in part}
        return [c for c in candidates if c not in bad]


def _dt(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _str(value: Any) -> str:
    """Coerce a NamUs field that is sometimes a string and sometimes a list.

    `ethnicities` comes back as a plain string from the MissingPersons case
    set and as a list of objects from UnidentifiedPersons. Normalising here
    keeps every consumer from having to know that.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [p.get("name", "") if isinstance(p, dict) else str(p) for p in value]
        return ", ".join(x for x in parts if x)
    if isinstance(value, dict):
        return str(value.get("name", ""))
    return str(value)


def _range(lo: Any, hi: Any) -> tuple[int, int] | None:
    vals = [int(v) for v in (lo, hi) if v is not None]
    if not vals:
        return None
    return (min(vals), max(vals))


def missing_case(number: int) -> dict[str, Any]:
    """Full record for one missing-person case, straight from the agency feed."""
    return get_json(f"{BASE}/MissingPersons/Cases/{number}")


def is_resolved(number: int) -> bool:
    return bool(missing_case(number).get("caseIsResolved"))


def find_missing(last: str, first: str | None = None,
                 states: list[str] | None = None) -> list[dict[str, Any]]:
    preds = [{"field": "stateOfLastContact", "operator": "IsIn", "values": states}] if states else []
    rows = _search("MissingPersons", MP_FIELDS, preds)
    hits = [r for r in rows if (r.get("lastName") or "").lower() == last.lower()]
    if first:
        hits = [r for r in hits if (r.get("firstName") or "").lower() == first.lower()]
    return hits


def unidentified(states: list[str] | None = None,
                 found_on_or_after: date | None = None) -> list[UnidentifiedRecord]:
    """Unidentified-person records, optionally narrowed by state of recovery.

    `found_on_or_after` filters client-side because the API rejects every
    date-range operator. Records with no dateFound are KEPT, not dropped: an
    unknown recovery date is not evidence the recovery predates the search.
    """
    preds = [{"field": "stateOfRecovery", "operator": "IsIn", "values": states}] if states else []
    rows = _search("UnidentifiedPersons", UP_FIELDS, preds)
    out: list[UnidentifiedRecord] = []
    for r in rows:
        found = _dt(r.get("dateFound"))
        if found_on_or_after and found and found < found_on_or_after:
            continue
        out.append(UnidentifiedRecord(
            case_id=r.get("idFormatted") or "",
            number=int(r.get("namus2Number") or 0),
            date_found=found,
            sex=_str(r.get("sex")) or "Unknown",
            ethnicity=_str(r.get("ethnicities")) or "Unknown",
            height_in=_range(r.get("heightFrom"), r.get("heightTo")),
            weight_lb=_range(r.get("weightFrom"), r.get("weightTo")),
            hair=_str(r.get("hairColor")) or "Unknown",
            eyes=_str(r.get("leftEyeColor")) or _str(r.get("rightEyeColor")) or "Unknown",
            age_est=_range(r.get("estimatedAgeFrom"), r.get("estimatedAgeTo")),
            state=_str(r.get("stateOfRecovery")),
            county=_str(r.get("countyOfRecovery")),
            city=_str(r.get("cityOfRecovery")),
            circumstances=_str(r.get("circumstancesOfRecovery")),
            modified=_str(r.get("modifiedDateTime")),
        ))
    return out
