"""One sweep over every source, tolerating any single source failing.

The watcher runs unattended. Before this module, a transient MSHP outage
raised out of the scan, nothing was saved, the note was not rewritten, and
the only record was a non-zero exit in a launchd log nobody reads. A source
that is down for one night should cost that night's reading of that source,
not the whole run, and a source that is down for several nights should be
reported as its own finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from missing_person.case import Case
from missing_person.match import Candidate, rank
from missing_person.net import SourceError
from missing_person.sources import mshp, namus, news
from missing_person.sources.mshp import MissingListing
from missing_person.sources.news import Article


@dataclass
class ScanResult:
    listings: list[MissingListing] = field(default_factory=list)
    namus_resolved: bool | None = None
    namus_modified: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    # source name -> error text, for every source that did not answer
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def ncic_checked(self) -> bool:
        return "mshp" not in self.failures

    def ncic_active(self, case: Case) -> bool | None:
        """None when the feed did not answer; never guess from silence."""
        if not self.ncic_checked:
            return None
        return any(case.mshp_last.lower() in m.name.lower()
                   and case.mshp_first.lower() in m.name.lower()
                   for m in self.listings)


def run(case: Case, min_score: float = 0.55,
        floor: date | None = None) -> ScanResult:
    r = ScanResult()
    try:
        r.listings = mshp.search_missing(case.mshp_last, case.mshp_first)
    except SourceError as exc:
        r.failures["mshp"] = str(exc)
    if case.namus_mp:
        try:
            record = namus.missing_case(case.namus_mp)
            r.namus_resolved = bool(record.get("caseIsResolved"))
            r.namus_modified = str(record.get("modifiedDateTime") or "")
        except SourceError as exc:
            r.failures["namus_case"] = str(exc)
    try:
        records = namus.unidentified(case.search_states,
                                     found_on_or_after=floor or case.recovery_floor)
        r.candidates = rank(case, records, minimum=min_score)
    except SourceError as exc:
        r.failures["namus_unidentified"] = str(exc)
    try:
        r.articles = news.scan(case.news_terms, case.mshp_last, case.locality_terms)
    except SourceError as exc:
        r.failures["news"] = str(exc)
    return r
