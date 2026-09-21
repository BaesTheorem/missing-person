"""Watch newsroom feeds for the case name.

FEEDS holds newsroom RSS for the region a case sits in; swap them per case.
The two below were verified reachable on 2026-09-16. Several large outlets
(KCTV5, the Kansas City Star) publish no usable feed or block automated
agents, so the Google News aggregator covers them indirectly.

Matching requires a name AND case context AND locality. Name alone is useless:
an unfiltered aggregator query for a common name returns athletes, writers,
and unrelated obituaries who happen to share it. Locality terms come from the
case file, because which place names anchor a case is case data, not code, and
they go into the query itself so the noise is cut at the source rather than
filtered after the fact.

A false positive here is not harmless. It is a notification telling a family
there is news when there is not.
"""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from missing_person.net import SourceError, fetch

FEEDS = {
    "KSHB 41": "https://www.kshb.com/news/local-news.rss",
    "FOX4 KC": "https://fox4kc.com/feed/",
}
GOOGLE_NEWS = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
CONTEXT = re.compile(
    r"\b(missing|disappear|search|remains|identif|body|found|vigil|police|investigat)",
    re.I,
)


@dataclass(frozen=True)
class Article:
    source: str
    title: str
    url: str
    published: str
    summary: str

    @property
    def key(self) -> str:
        return self.url.split("?")[0]


def _text(node: ET.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _items(xml: bytes, source: str) -> list[Article]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise SourceError(f"{source}: feed is not parseable XML ({exc})") from exc
    out: list[Article] = []
    for item in root.iter("item"):
        out.append(Article(
            source=source,
            title=_text(item.find("title")),
            url=_text(item.find("link")),
            published=_text(item.find("pubDate")),
            summary=re.sub(r"<[^>]+>", " ", _text(item.find("description")))[:400],
        ))
    return out


def locality_pattern(terms: list[str]) -> re.Pattern[str]:
    if not terms:
        # No anchors configured: fall back to context alone rather than
        # matching everything, and let the caller see the looser behaviour.
        return re.compile(r"(?!)")
    return re.compile("|".join(re.escape(t) for t in terms), re.I)


@dataclass(frozen=True)
class CaseFacts:
    """The case facts that can identify a story whose headline omits the name.

    A local newsroom writes "Deputies searching for missing 30-year-old man" far
    more often than it writes the name, and a Google News <description> is a
    stub (the headline again plus the publication), so the body text that
    WOULD carry the name never reaches the matcher. Requiring the name on that
    input discards exactly the coverage the watcher exists to catch.

    These fields are what remains to identify a story by. They are case data,
    not code, for the same reason locality terms are.
    """

    agency_terms: list[str] = field(default_factory=list)
    age: int | None = None
    last_seen: date | None = None


AGE_MENTION = re.compile(r"\b(\d{1,3})[\s-]?year[\s-]?old\b", re.I)


def _date_forms(day: date) -> list[str]:
    """Every way a newsroom writes this date, across a one-day window.

    The window is not slack, it is the disagreement these cases actually
    carry: the NCIC entry and the first news story routinely name different
    days for the same disappearance.
    """
    out: list[str] = []
    for delta in (-1, 0, 1):
        d = day + timedelta(days=delta)
        out += [f"{d:%B} {d.day}", f"{d:%b} {d.day}",
                f"{d.month}/{d.day}", f"{d:%m/%d}"]
    return out


def corroborations(article: Article, facts: CaseFacts) -> list[str]:
    """Which case-specific facts this story independently agrees with.

    Only facts a story could get wrong count. Locality does not appear here:
    it is already required of every candidate, so counting it again would let
    a generic local crime item look corroborated.
    """
    blob = f"{article.title} {article.summary}"
    low = blob.lower()
    hits: list[str] = []
    if any(t.lower() in low for t in facts.agency_terms):
        hits.append("agency")
    if facts.age is not None and any(
            int(m) == facts.age for m in AGE_MENTION.findall(blob)):
        hits.append("age")
    if facts.last_seen is not None:
        if any(f.lower() in low for f in _date_forms(facts.last_seen)):
            hits.append("date")
        elif re.search(
                r"(last seen|missing since|last contact|disappear|vanish)"
                r"[^.]{0,40}?\b" + f"{facts.last_seen:%A}" + r"\b", blob, re.I):
            hits.append("weekday")
    return hits


def contradictions(article: Article, facts: CaseFacts) -> list[str]:
    """Facts this story states that rule it OUT, so noise cannot accumulate.

    A story about a different missing person in the same city matches locality
    and context and names an agency. The age it prints is the cheapest thing
    that separates it, and treating a stated mismatch as disqualifying is what
    keeps "missing 47-year-old man" from riding agency agreement into a
    notification.
    """
    blob = f"{article.title} {article.summary}"
    ages = {int(m) for m in AGE_MENTION.findall(blob)}
    if facts.age is not None and ages and facts.age not in ages:
        return [f"age {'/'.join(str(a) for a in sorted(ages))} != {facts.age}"]
    return []


@dataclass(frozen=True)
class NewsCandidate:
    """A local missing-person story that never printed the name.

    Never a match and never state. It is a prompt to go read something, which
    is why `strong` gates notification and nothing here feeds a conclusion.
    """

    article: Article
    corroborates: list[str]
    contradicts: list[str]

    @property
    def strong(self) -> bool:
        return len(self.corroborates) >= 2 and not self.contradicts


def _matches(article: Article, terms: list[str], surname: str,
             locality: list[str] | None = None) -> bool:
    """A hit needs the name, case context, AND local anchoring.

    Dropping any one of the three floods the result with unrelated people who
    share a common name.
    """
    blob = f"{article.title} {article.summary}"
    low = blob.lower()
    named = any(t.lower() in low for t in terms) or surname.lower() in low
    if not named:
        return False
    if not CONTEXT.search(blob):
        return False
    return bool(locality_pattern(locality or []).search(blob))


def scan(terms: list[str], surname: str, locality: list[str] | None = None,
         facts: CaseFacts | None = None) -> tuple[list[Article], list[NewsCandidate]]:
    """Sweep every feed, returning confirmed mentions and name-less candidates.

    Two buckets, because the feeds supply two different kinds of evidence and
    collapsing them loses one of them. A story that prints the name is a
    mention. A local missing-person story that does not print the name is a
    candidate: worth a human read, never worth a conclusion.

    A dead feed is reported, never treated as 'no news'.
    """
    hits: list[Article] = []
    maybes: list[NewsCandidate] = []
    problems: list[str] = []
    feeds = dict(FEEDS)
    anchor = " OR ".join(f'"{t}"' for t in (locality or [])[:3])
    for term in terms[:2]:
        query = f'"{term}" (missing{" OR " + anchor if anchor else ""})'
        feeds[f"News: {term}"] = GOOGLE_NEWS.format(urllib.parse.quote(query))
    for source, url in feeds.items():
        try:
            for article in _items(fetch(url, timeout=40, retries=2), source):
                if _matches(article, terms, surname, locality):
                    hits.append(article)
                elif facts and _is_candidate(article, locality):
                    maybes.append(NewsCandidate(
                        article, corroborations(article, facts),
                        contradictions(article, facts)))
        except SourceError as exc:
            problems.append(f"{source}: {exc}")
    if problems and not hits and not maybes:
        # An empty result from a broken instrument is not evidence of silence.
        raise SourceError("no feed answered: " + "; ".join(problems))
    seen: set[str] = set()
    unique = [a for a in hits if not (a.key in seen or seen.add(a.key))]
    unique.sort(key=_sort_key, reverse=True)
    kept: set[str] = set()
    cands = [c for c in maybes
             if c.corroborates and not (c.article.key in kept or kept.add(c.article.key))]
    cands.sort(key=lambda c: (c.strong, len(c.corroborates),
                              _sort_key(c.article)), reverse=True)
    return unique, cands


def _is_candidate(article: Article, locality: list[str] | None) -> bool:
    """Local, and about a missing person, but the name never appears.

    Deliberately the same context and locality gates `_matches` applies. Only
    the name leg is dropped, and what replaces it is corroboration scoring,
    not nothing.
    """
    blob = f"{article.title} {article.summary}"
    if not CONTEXT.search(blob):
        return False
    return bool(locality_pattern(locality or []).search(blob))


def _sort_key(article: Article) -> datetime:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(article.published, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return datetime.min
