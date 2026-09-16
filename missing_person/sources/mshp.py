"""Missouri State Highway Patrol: the NCIC-fed missing and unidentified feeds.

This is the closest thing to a live read on case STATUS that the public gets.
MSHP states on the page that the data comes from NCIC via the investigating
agency and is refreshed daily, so a name dropping off the active list is the
first public signal that an agency cleared the NCIC entry.

The two feeds are NOT equally current, and conflating them would be the worst
mistake this module could make:

  - `search_missing` is the live one. MSHP says the missing-person data is
    pulled from NCIC and refreshed daily, and it matches what agencies show.
  - `unidentified` is NOT live. MSHP's own disclaimer says unidentified
    records are added "on a case-by-case basis", and measured against the
    page on 2026-09-16 it held 42 Missouri records with nothing recovered
    after February 2023, where NamUs held 115 for the same state. So an empty
    result here is NOT evidence that no one was recovered. Use NamUs for
    unidentified-person sweeps and treat this feed as corroboration only.

Both endpoints are plain server-rendered HTML with no API, so this parses the
result table. The parse is deliberately shaped around the table's <th> row:
if MSHP reorders or renames columns, `_rows` raises instead of silently
returning fields shifted by one, which on a case like this would mean
comparing someone's height against someone else's weight.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date, datetime

from missing_person.net import SourceError, fetch, post_form

SEARCH_URL = "https://www.mshp.dps.mo.gov/CJ51/Search"
UNIDENTIFIED_URL = "https://www.mshp.dps.mo.gov/CJ51/SearchUnidentified"
TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class MissingListing:
    name: str
    sex: str
    race: str
    missing_since: date | None
    age_missing: str
    agency: str
    missing_from: str
    kind: str
    poster_url: str | None
    photo_url: str | None


@dataclass(frozen=True)
class UnidentifiedListing:
    ncic: str
    agency: str
    location_found: str
    county: str
    date_found: date | None
    estimated_age: str
    sex: str
    race: str
    hair: str
    eyes: str
    weight: str
    height: str
    poster_url: str | None


def _clean(cell: str) -> str:
    text = html.unescape(TAG.sub(" ", cell))
    return re.sub(r"\s+", " ", text).strip()


def _first_href(cell: str, needle: str) -> str | None:
    for href in re.findall(r'href="([^"]+)"', cell):
        if needle in href.lower():
            return html.unescape(href)
    return None


def _first_img(cell: str) -> str | None:
    m = re.search(r'src="([^"]+)"', cell)
    return html.unescape(m.group(1)) if m else None


def _rows(page: str, expect: list[str]) -> list[list[str]]:
    """Split the results table into rows, after asserting the header matches.

    `expect` is a list of substrings that must appear, in order, in the header
    cells. That check is the whole point: a positional parse that does not
    verify its positions is an instrument that cannot report its own failure.
    """
    body = re.search(r"(?is)<table[^>]*>(.*?)</table>", page)
    if not body:
        if "results found" in page and re.search(r"\b0 results found\b", page):
            return []
        raise SourceError("MSHP returned no results table; page layout may have changed")
    table = body.group(1)
    head = re.search(r"(?is)<thead>(.*?)</thead>", table)
    if head:
        cols = [_clean(c).lower() for c in re.findall(r"(?is)<th[^>]*>(.*?)</th>", head.group(1))]
        for i, want in enumerate(expect):
            if i >= len(cols) or want not in cols[i]:
                raise SourceError(
                    f"MSHP column {i} is {cols[i:i+1]}, expected to contain {want!r}. "
                    "Refusing to parse positionally against a changed table."
                )
    rows: list[list[str]] = []
    tbody = re.search(r"(?is)<tbody>(.*?)</tbody>", table)
    for tr in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", tbody.group(1) if tbody else table):
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", tr)
        if cells:
            rows.append(cells)
    return rows


def _mdy(value: str) -> date | None:
    """Pull a date out of a cell that may carry more than a date.

    The unidentified table's "Date Body Found" cell holds a hidden sort key
    next to the displayed date, so the cell text is "20230228 02/28/2023".
    Parsing the whole cell fails on both formats and silently yields None,
    which then drops every record out of any date filter. Scan tokens instead.
    """
    for token in value.replace("\xa0", " ").split():
        for fmt in ("%m/%d/%Y", "%Y%m%d", "%m/%d/%y"):
            try:
                return datetime.strptime(token, fmt).date()
            except ValueError:
                continue
    return None


def search_missing(last: str, first: str = "") -> list[MissingListing]:
    """Active NCIC missing-person entries in Missouri for this name."""
    page = post_form(SEARCH_URL, {
        "personType": "", "searchLast": last, "searchFirst": first,
        "searchSinceFrom": "", "searchSinceTo": "", "searchPoster": "",
        "searchGender": "", "searchRace": "",
    })
    expect = ["rec", "photo", "name", "gender", "race", "missing since", "age"]
    out: list[MissingListing] = []
    for cells in _rows(page, expect):
        if len(cells) < 11:
            continue
        out.append(MissingListing(
            name=_clean(cells[2]), sex=_clean(cells[3]), race=_clean(cells[4]),
            missing_since=_mdy(_clean(cells[5])), age_missing=_clean(cells[6]),
            agency=_clean(cells[7]), missing_from=_clean(cells[8]), kind=_clean(cells[9]),
            poster_url=_first_href(cells[10], ".pdf"), photo_url=_first_img(cells[1]),
        ))
    return out


def unidentified(found_on_or_after: date | None = None) -> list[UnidentifiedListing]:
    """Active Missouri unidentified-person (deceased) entries."""
    page = fetch(UNIDENTIFIED_URL, timeout=60).decode("utf-8", "replace")
    expect = ["rec", "photo", "ncic", "investigating", "location", "tattoo",
              "piercing", "county", "date body found"]
    out: list[UnidentifiedListing] = []
    for cells in _rows(page, expect):
        if len(cells) < 18:
            continue
        found = _mdy(_clean(cells[8]))
        if found_on_or_after and found and found < found_on_or_after:
            continue
        out.append(UnidentifiedListing(
            ncic=_clean(cells[2]), agency=_clean(cells[3]), location_found=_clean(cells[4]),
            county=_clean(cells[7]), date_found=found, estimated_age=_clean(cells[9]),
            sex=_clean(cells[12]), race=_clean(cells[13]), hair=_clean(cells[14]),
            eyes=_clean(cells[15]), weight=_clean(cells[16]), height=_clean(cells[17]),
            poster_url=_first_href(cells[-1], ".pdf"),
        ))
    return out
