"""Rank unidentified-person records as CANDIDATES against a case.

This produces leads for a human to send to the investigating agency. It never
produces an identification, and nothing downstream is allowed to present it
as one. Identification is DNA, dental, prints, or a medical examiner; a
demographic overlap is only a reason to look.

The scoring rule that matters most: UNKNOWN SCORES NEUTRAL, never against.
Unidentified records are mostly unknowns, and the records most likely to
belong to a long-missing person -- skeletal remains, river recoveries -- are
the ones with the fewest known fields. A scorer that penalises missing data
sorts exactly the right candidates to the bottom. So each dimension returns
None when the record cannot answer it, and the final score is the mean of the
dimensions that could.

A hard conflict (wrong sex, recovered before the person went missing) is
different from an unknown and does exclude.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from missing_person.case import Case
from missing_person.sources.namus import UnidentifiedRecord

UNKNOWNS = {"", "unknown", "unsure", "uncertain", "unk", "not applicable", "other"}

WEIGHTS = {"age": 2.0, "height": 2.0, "weight": 1.5, "hair": 1.0, "eyes": 1.0, "race": 1.0}


@dataclass(frozen=True)
class Candidate:
    record: UnidentifiedRecord
    score: float
    coverage: float
    matched: list[str]
    unknown: list[str]
    note: str

    @property
    def rank_key(self) -> float:
        """Score, discounted by how much of the description was checkable.

        A record whose only known field is height should not outrank a record
        that agrees on five fields, even when that one field scores well. The
        discount never reaches zero, so sparse records stay on the list rather
        than being filtered out -- which is the whole point, since the records
        most likely to belong to someone missing this long are the sparsest.
        """
        return self.score * (0.5 + 0.5 * self.coverage)

    @property
    def stars(self) -> str:
        return "*" * max(1, min(5, round(self.rank_key * 5)))


def _known(value: str) -> bool:
    return value.strip().lower() not in UNKNOWNS


def _overlap(a: tuple[int, int], b: tuple[int, int], tolerance: int) -> float | None:
    """1.0 when the ranges intersect, decaying to 0 across `tolerance`."""
    lo_a, hi_a = a[0] - tolerance, a[1] + tolerance
    if lo_a <= b[1] and b[0] <= hi_a:
        tight_lo, tight_hi = a
        if tight_lo <= b[1] and b[0] <= tight_hi:
            return 1.0
        gap = max(b[0] - tight_hi, tight_lo - b[1])
        return max(0.0, 1.0 - gap / (tolerance + 1))
    return 0.0


def _hard_conflict(case: Case, rec: UnidentifiedRecord) -> str | None:
    """Reasons to exclude outright, as opposed to score low.

    The recovery floor must be the LAST CONFIRMED-ALIVE moment, not the
    earliest date any source mentions. An early floor "to be inclusive" let a
    skull found 4/26 through for a man who was on camera 4/30. Inclusive
    toward the past is not inclusive; it is wrong.

    Not handled here, and worth knowing: a record's dateFound is sometimes a
    lab-intake date rather than a recovery date (four Missouri records carried
    "Date Body Found is the date of arrival at the SEMO Human Osteology Lab").
    Skeletal remains at a lab days after the floor cannot be the person. Read
    circumstancesOfRecovery before treating a local, recent, all-unknown record
    as a lead.
    """
    want_sex = case.description.sex.strip().lower()
    got_sex = rec.sex.strip().lower()
    if _known(got_sex) and got_sex != want_sex:
        return f"sex {rec.sex} != {case.description.sex}"
    if rec.date_found and rec.date_found < case.recovery_floor:
        return f"recovered {rec.date_found} before floor {case.recovery_floor}"
    return None


def score(case: Case, rec: UnidentifiedRecord, today: date | None = None) -> Candidate | None:
    if _hard_conflict(case, rec):
        return None
    today = today or date.today()
    d = case.description
    parts: dict[str, float] = {}
    unknown: list[str] = []

    if rec.age_est:
        # Age on a UP record is estimated at recovery, so compare against how
        # old he would have been THEN, not now.
        ref = rec.date_found or today
        years = (ref - case.last_seen_date).days / 365.25
        age_then = d.age_at_disappearance + max(0.0, years)
        parts["age"] = _overlap((int(age_then), int(age_then) + 1), rec.age_est, 5) or 0.0
    else:
        unknown.append("age")

    if rec.height_in:
        parts["height"] = _overlap(d.height_in, rec.height_in, 2) or 0.0
    else:
        unknown.append("height")

    if rec.weight_lb:
        parts["weight"] = _overlap(d.weight_lb, rec.weight_lb, 25) or 0.0
    else:
        unknown.append("weight")

    for key, want, got in (("hair", d.hair, rec.hair), ("eyes", d.eyes, rec.eyes),
                           ("race", d.race, rec.ethnicity)):
        if _known(got):
            parts[key] = 1.0 if want.strip().lower() in got.strip().lower() else 0.0
        else:
            unknown.append(key)

    if not parts:
        # Nothing comparable at all. Still a lead if it is local and recent,
        # but say so rather than inventing a number.
        return Candidate(rec, 0.35, 0.0, [], unknown,
                         "no comparable fields; ranked on geography and timing alone")

    total = sum(WEIGHTS[k] * v for k, v in parts.items())
    denom = sum(WEIGHTS[k] for k in parts)
    coverage = denom / sum(WEIGHTS.values())
    # "Matched" means the ranges actually intersect. A near miss still earns
    # partial score, but calling it a match is how a 5'11" record ends up
    # advertised as agreeing with a 5'2" description.
    matched = [k for k, v in parts.items() if v >= 0.999]
    return Candidate(rec, total / denom, coverage, matched, unknown, "")


def rank(case: Case, records: list[UnidentifiedRecord],
         minimum: float = 0.0) -> list[Candidate]:
    out = [c for c in (score(case, r) for r in records) if c and c.score >= minimum]
    out.sort(key=lambda c: (-c.rank_key, c.record.date_found or date.min))
    return out
