"""Positive and negative controls for the matchers.

Every assertion here exists because the corresponding failure mode is SILENT
in production. A news filter that matches nothing and a news filter that is
broken produce identical output ("no mentions"). A candidate scorer that
excludes sparse records and one that genuinely found nothing both return an
empty list. These tests are what makes the difference observable.

Everything runs against the synthetic example case. No real case data lives in
this repository, so the suite must not reach for any.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
# Pin case lookup to this repo's example, so the suite cannot pick up a real
# case from a vault that happens to exist on the machine running it.
os.environ["MP_CASES_DIR"] = str(REPO / "cases")

from missing_person import case as case_mod
from missing_person.geo import separation, zip_conflicts
from missing_person import scan as scan_mod
from missing_person.match import rank, score
from missing_person.net import SourceError
from missing_person import documents as docs_mod
from missing_person.sources import mshp, news
from missing_person.sources.namus import UnidentifiedRecord

TERMS = ["Example Person"]
SURNAME = "Person"
LOCALITY = ["Kansas City", "Platte County", "Northland"]


@pytest.fixture
def example() -> case_mod.Case:
    return case_mod.load("example")


def _article(title: str, summary: str = "") -> news.Article:
    return news.Article("test", title, "https://example.invalid/x", "", summary)


# --- case resolution --------------------------------------------------------

def test_missing_case_says_where_it_looked() -> None:
    """A bare 'file not found' sends someone hunting for a path this module
    already knows. The error names every directory searched."""
    with pytest.raises(FileNotFoundError) as excinfo:
        case_mod.load("no-such-case")
    assert "Searched" in str(excinfo.value)


def test_example_case_is_the_only_one_shipped() -> None:
    """Guards the reason this repo is safe to publish: a real case names a
    living person, so real case files belong outside it."""
    shipped = sorted(p.stem for p in (REPO / "cases").glob("*.toml"))
    assert shipped == ["example"]


# --- news matcher -----------------------------------------------------------

def test_news_matches_a_real_story_about_the_case() -> None:
    """The control that makes 'no mentions' mean something."""
    hit = _article(
        "Kansas City, Missouri, police searching for 30-year-old missing man",
        "Example Person was last talked to around 10:17 p.m. Wednesday. Police say "
        "the family is concerned for their well-being.",
    )
    assert news._matches(hit, TERMS, SURNAME, LOCALITY)


def test_news_matches_a_recovery_story() -> None:
    hit = _article("Remains found in Platte County identified as missing "
                   "Kansas City man Example Person")
    assert news._matches(hit, TERMS, SURNAME, LOCALITY)


@pytest.mark.parametrize("title", [
    # Each mirrors a real false positive seen in production: an unfiltered
    # query on a common name returns athletes, writers and obituaries.
    "Example Person - McAdory Yellowjackets Baseball (McCalla, AL)",
    "A very postmodern schism | Example Person - The Critic",
    "Example Person - Transfermarkt",
    "Example Person of Tennessee High with the long kick.",
    "Bradley Example Person Obituary (1993 - 2026) - Ladson, SC",
])
def test_news_rejects_unrelated_people_with_the_same_name(title: str) -> None:
    assert not news._matches(_article(title), TERMS, SURNAME, LOCALITY)


def test_news_rejects_a_story_with_no_local_anchor() -> None:
    """Right name, right context, wrong place. Without the locality gate this
    matched a same-named missing person in another state."""
    assert not news._matches(
        _article("Search continues for missing man Example Person in Oregon"),
        TERMS, SURNAME, LOCALITY)


def test_news_rejects_local_story_about_someone_else() -> None:
    """Local AND about a missing person, but not this one. Name is required."""
    assert not news._matches(
        _article("Kansas City police searching for missing 27-year-old Northland woman"),
        TERMS, SURNAME, LOCALITY)


# --- MSHP parsing -----------------------------------------------------------

def test_date_cell_with_hidden_sort_key_parses() -> None:
    """The cell reads '20230228 02/28/2023'. Parsing the whole string yields
    None, which drops every record out of any date filter without erroring."""
    assert mshp._mdy("20230228 02/28/2023") == date(2023, 2, 28)
    assert mshp._mdy("02/28/2023") == date(2023, 2, 28)
    assert mshp._mdy("Unknown") is None


def test_reordered_columns_raise_instead_of_returning_shifted_data() -> None:
    """A positional parse that cannot detect a layout change would compare one
    person's height against another's weight and report it as agreement."""
    page = ("<table><thead><tr><th>Name</th><th>Rec#</th></tr></thead>"
            "<tbody><tr><td>a</td><td>b</td></tr></tbody></table>")
    with pytest.raises(SourceError):
        mshp._rows(page, ["rec", "photo", "ncic"])


# --- candidate scoring ------------------------------------------------------

def _up(**kw) -> UnidentifiedRecord:
    """A record that agrees with the example case on every field."""
    base = dict(case_id="UP1", number=1, date_found=date(2026, 3, 1), sex="Male",
                ethnicity="White / Caucasian", height_in=(69, 69), weight_lb=(160, 160),
                hair="Brown", eyes="Blue", age_est=(28, 34), state="Missouri",
                county="Jackson", city="", circumstances="", modified="")
    base.update(kw)
    return UnidentifiedRecord(**base)  # type: ignore[arg-type]


def test_close_match_scores_high(example: case_mod.Case) -> None:
    got = score(example, _up())
    assert got is not None and got.score > 0.9


def test_wrong_sex_is_excluded(example: case_mod.Case) -> None:
    assert score(example, _up(sex="Female")) is None


def test_recovery_before_the_floor_is_excluded(example: case_mod.Case) -> None:
    assert score(example, _up(date_found=date(2026, 1, 1))) is None


def test_unknown_sex_is_not_excluded(example: case_mod.Case) -> None:
    """Skeletal remains often carry 'Unsure'. Excluding them would drop exactly
    the records most likely to belong to someone missing a long time."""
    assert score(example, _up(sex="Unsure")) is not None


def test_sparse_record_survives_but_ranks_below_a_dense_agreement(
        example: case_mod.Case) -> None:
    sparse = _up(case_id="UP-sparse", height_in=None, weight_lb=None, age_est=None,
                 hair="Unknown", eyes="Unknown", ethnicity="Uncertain")
    ranked = rank(example, [sparse, _up(case_id="UP-dense")])
    assert [c.record.case_id for c in ranked] == ["UP-dense", "UP-sparse"]
    assert len(ranked) == 2, "a sparse record must stay on the list, not be filtered out"


def test_one_weak_field_does_not_outrank_broad_agreement(example: case_mod.Case) -> None:
    """A record agreeing on a single loose height range outranked a full match
    before coverage weighting was added."""
    thin = _up(case_id="UP-thin", height_in=(66, 73), weight_lb=None, age_est=None,
               hair="Unknown", eyes="Unknown", ethnicity="Uncertain")
    ranked = rank(example, [thin, _up(case_id="UP-full")])
    assert ranked[0].record.case_id == "UP-full"


def test_matched_means_the_ranges_actually_intersect(example: case_mod.Case) -> None:
    """Height 74-78 against 68-70 earns partial score for proximity, but calling
    it 'matched' is how a six-footer gets advertised as agreeing with a 5'8"."""
    got = score(example, _up(height_in=(74, 78)))
    assert got is not None and "height" not in got.matched


# --- case file shape --------------------------------------------------------

def test_case_keeps_competing_location_claims(example: case_mod.Case) -> None:
    assert len(example.locations) >= 2, "reconciling claims by fiat loses the disagreement"


def test_recovery_floor_is_not_later_than_the_last_seen_date(
        example: case_mod.Case) -> None:
    assert example.recovery_floor <= example.last_seen_date


# --- positional precision ---------------------------------------------------

def _loc(label: str, lat: float, lon: float, precision: float,
         zcta: str = "") -> case_mod.Location:
    return case_mod.Location(label=label, source="test", lat=lat, lon=lon,
                             precision_mi=precision, zcta=zcta)


def test_gap_inside_combined_uncertainty_is_not_called_meaningful() -> None:
    """The bug this encodes: a coarsened centroid was differenced against a
    street intersection and the 2.61 mi result was reported as a finding. The
    combined uncertainty was 2.75 mi, so the measurement said nothing."""
    coarse = _loc("ZIP centroid", 39.2183, -94.6347, 2.5)
    precise = _loc("intersection", 39.2542, -94.6498, 0.25)
    (_, _, miles, meaningful), = separation([coarse, precise])
    assert 2.5 < miles < 2.8
    assert not meaningful


def test_gap_exceeding_uncertainty_is_meaningful() -> None:
    a = _loc("a", 39.10, -94.60, 0.25)
    b = _loc("b", 39.30, -94.60, 0.25)
    (_, _, _, meaningful), = separation([a, b])
    assert meaningful


def test_differing_zips_are_flagged_even_when_distance_cannot_resolve() -> None:
    """The whole point of carrying ZIP separately: the distance test abstains,
    the ZIP test still answers."""
    coarse = _loc("ZIP centroid", 39.2183, -94.6347, 2.5, "64151")
    precise = _loc("intersection", 39.2542, -94.6498, 0.25, "64154")
    (_, _, _, meaningful), = separation([coarse, precise])
    assert not meaningful
    assert zip_conflicts([coarse, precise]) == [
        ("ZIP centroid", "64151", "intersection", "64154")]


def test_missing_zip_is_not_a_conflict() -> None:
    """An unknown ZIP must not read as a disagreement."""
    assert zip_conflicts([_loc("a", 39.1, -94.6, 0.25, ""),
                          _loc("b", 39.2, -94.6, 0.25, "64154")]) == []


# --- source documents -------------------------------------------------------

def test_garbage_extraction_is_rejected() -> None:
    """The control that would have caught the subset-font bug.

    This fixture is the REAL failure signature, not a tidied version of it. A
    byte-level read of a Type0 subset font leaves the high NUL between every
    glyph and shifts the visible bytes off ASCII. An earlier version of this
    test used a space-separated fixture, which was easier to read and which
    two different broken heuristics both passed.
    """
    naive = "".join(f"\x00{c}" for c in "81&/$66,),(\x12\x1238%/,&$:$5(1(66")
    assert not docs_mod.looks_like_text(naive)


def test_all_digits_is_not_a_successful_decode() -> None:
    assert not docs_mod.looks_like_text("1234567890 " * 10)


def test_real_prose_passes_the_text_check() -> None:
    assert docs_mod.looks_like_text(
        "Jacob was last seen on 04/22/2026 at 4:00pm in the 8600 block of "
        "N Helena Ave in Kansas City, Missouri.")


def test_too_short_to_judge_is_not_accepted() -> None:
    """A handful of characters cannot demonstrate a working decode."""
    assert not docs_mod.looks_like_text("abc")


def test_extraction_refuses_rather_than_returns_unreadable_output(tmp_path) -> None:
    """A fallback that fails loudly is fine. One that lies is worse than none."""
    bogus = tmp_path / "x.pdf"
    bogus.write_bytes(b"%PDF-1.4\nstream\nBT (\x00A\x00B) Tj ET\nendstream\n")
    with pytest.raises(SourceError):
        docs_mod.extract(bogus)


def test_tounicode_bfrange_is_expanded() -> None:
    """bfrange maps a span of glyph codes; reading only bfchar loses most text."""
    stream = (b"begincmap 2 beginbfrange\n<0003><0003><0020>\n"
              b"<0024><0026><0041>\nendbfrange endcmap")
    cmap = docs_mod._tounicode([stream])
    assert cmap[0x03] == " "
    assert cmap[0x24] == "A"
    assert cmap[0x26] == "C", "the range end must be inclusive"


def test_tounicode_bfchar_pairs() -> None:
    stream = b"begincmap 1 beginbfchar\n<0041><0061>\nendbfchar endcmap"
    assert docs_mod._tounicode([stream])[0x41] == "a"


def test_document_slug_is_stable_and_filesystem_safe() -> None:
    d = docs_mod.Document(label="MSHP/NCIC Bulletin (PDF)", url="https://x.invalid/a.pdf")
    assert d.slug == "mshp-ncic-bulletin-pdf"


def test_documents_are_stored_beside_the_case_not_in_the_repo(tmp_path) -> None:
    """Source documents are case material about a real person."""
    cases = tmp_path / "cases"
    where = docs_mod.store_dir("some-case", cases)
    assert where == tmp_path / "documents" / "some-case"
    assert REPO not in where.parents


# --- scan tolerates a dead source -------------------------------------------

def test_scan_survives_mshp_outage(example: case_mod.Case, monkeypatch) -> None:
    """A transient outage of one feed used to raise out of the whole run, so
    nothing was saved and the only trace was a non-zero exit in a launchd log."""
    def boom(*_a, **_k):
        raise SourceError("MSHP returned no results table")
    monkeypatch.setattr(scan_mod.mshp, "search_missing", boom)
    monkeypatch.setattr(scan_mod.namus, "unidentified", lambda *a, **k: [])
    monkeypatch.setattr(scan_mod.news, "scan", lambda *a, **k: [])
    r = scan_mod.run(example)
    assert "mshp" in r.failures
    assert r.ncic_active(example) is None, "a feed that did not answer is not 'inactive'"


def test_scan_reports_active_when_feed_answers(example: case_mod.Case, monkeypatch) -> None:
    from missing_person.sources.mshp import MissingListing
    hit = MissingListing(name="PERSON, EXAMPLE Q", sex="Male", race="White",
                         missing_since=None, age_missing="30", agency="X", missing_from="Y",
                         kind="ADULT", poster_url=None, photo_url=None)
    monkeypatch.setattr(scan_mod.mshp, "search_missing", lambda *a, **k: [hit])
    monkeypatch.setattr(scan_mod.namus, "unidentified", lambda *a, **k: [])
    monkeypatch.setattr(scan_mod.news, "scan", lambda *a, **k: [])
    r = scan_mod.run(example)
    assert r.ncic_active(example) is True
