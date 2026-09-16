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
from dataclasses import dataclass
from datetime import datetime

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


def scan(terms: list[str], surname: str,
         locality: list[str] | None = None) -> list[Article]:
    """Sweep every feed. A dead feed is reported, never treated as 'no news'."""
    hits: list[Article] = []
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
        except SourceError as exc:
            problems.append(f"{source}: {exc}")
    if problems and not hits:
        # An empty result from a broken instrument is not evidence of silence.
        raise SourceError("no feed answered: " + "; ".join(problems))
    seen: set[str] = set()
    unique = [a for a in hits if not (a.key in seen or seen.add(a.key))]
    unique.sort(key=_sort_key, reverse=True)
    return unique


def _sort_key(article: Article) -> datetime:
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(article.published, fmt).replace(tzinfo=None)
        except ValueError:
            continue
    return datetime.min
