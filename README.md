# missing-person

A toolkit for assisting a **public** missing-person search: track case status
across the databases that would change first if someone were found, sweep
unidentified-person records for candidates, watch newsrooms, and map the
terrain around the last-seen point.

Built while helping with a real search. The case is a config file, so it
generalises to any case in the United States.

## Scope, deliberately narrow

Every source is public and read-only: government databases published for
exactly this purpose (NCIC via state feeds, NamUs), newsrooms' own RSS, and
OpenStreetMap. Nothing touches private accounts, phone data, or the people
around a missing person.

The toolkit cannot file, edit, or close a case, and **it does not identify
anyone**. Unidentified-person output is a list of ranked leads for the
investigating agency to evaluate. Identification is DNA, dental, prints, or a
medical examiner.

## Install

Python 3.11+. Standard library only. No venv, no dependencies.

    git clone https://github.com/BaesTheorem/missing-person
    cd missing-person
    bin/mp cases

## Use

    bin/mp cases                  every case found in the search directories
    bin/mp status <case>          NCIC + NamUs status, and the source conflicts
    bin/mp unidentified <case>    rank unidentified-person candidates
    bin/mp news <case>            newsroom sweep
    bin/mp area <case>            terrain around each last-seen claim
    bin/mp scan <case>            everything, into a markdown note
    bin/mp watch <case>           scan, notify ONLY on a change

## Where case files live

A real case names a living person and describes them, so **real case files are
kept outside this repository.** Only a synthetic `cases/example.toml` ships
here. Case ids resolve in this order:

1. `$MP_CASES_DIR`
2. `~/Exobrain/Areas/Community/Missing Persons/cases` (a notes vault)
3. `./cases` in this repo (example only)

Directories are searched in order and unioned, so a private case shadows a
same-named example. Copy `cases/example.toml`, fill it in, and put it in one
of the first two.

## What each source is actually good for

Measured 2026-09-16. "It's in the database" and "the database is current" are
different claims, and only one of them helps.

| Source | Good for | Not good for |
|---|---|---|
| **State NCIC feed** (e.g. MSHP, daily) | Live case status. A name dropping off the active list is the first public signal an agency cleared the NCIC entry. | One state at a time. |
| **State unidentified feed** | Corroboration. | **Currency.** Missouri's is added "case-by-case": 42 records, nothing recovered after Feb 2023, where NamUs held 115 for the same state. An empty result there is *not* evidence nobody was recovered. |
| **NamUs** (`www.namus.gov/api/`) | Both case status (`caseIsResolved`) and the live unidentified sweep. Entry lag across the seven states measured has fallen to a **16-day median, 93% within 90 days** for 2026 recoveries, so a clean sweep now is real evidence. | Full national pulls are slow (~15.5k records). |
| **Newsroom RSS + aggregator** | New coverage. | History. RSS carries recent items only, so a quiet result says nothing about six months ago. |
| **Overpass / OSM** | Terrain for ground search. | Predicting where anyone is. It lists water and woods, that is all. |

### API notes worth not rediscovering

- NamUs moved. `namus.nij.ojp.gov/api/` 404s; the live host is
  `www.namus.gov/api/`.
- Its search endpoint validates projection names and names every bad one in
  the 400 body, which makes it self-documenting.
  `namus.discover_fields()` rebuilds the field list in one call when it drifts.
- **`IsIn` is the only predicate operator that works.** `IsEqualTo`,
  `Contains`, and every date-range operator (`GreaterThan`, `IsOnOrAfter`,
  `Between`) return HTTP 500. Date filtering happens client-side.
- `ethnicities` is a string on MissingPersons and a list on
  UnidentifiedPersons. `namus._str` normalises it.
- State feeds may 403 a bare urllib User-Agent, and Missouri's "Date Body
  Found" cell hides a sort key beside the date (`"20230228 02/28/2023"`).
- Overpass's main instance beats the kumi and private.coffee mirrors, which
  time out more often than they answer. Expect slow, not failed.

## Design decisions that are load-bearing

**Competing claims are kept, not reconciled.** Sources routinely disagree
about where and when someone was last seen. In the case this was built for,
the family flyer and the agency-fed record were **5.4 miles apart**. Both go
in the case file with their provenance; `mp status` prints the distance
between them. Picking a favourite would hide a five-mile search-area question
behind a tidy config.

**Descriptions are ranges.** Three sources gave three different heights for
the same person. Filtering on any one number discards the candidate the others
would have caught.

**Unknown scores neutral, never against.** Unidentified records are mostly
unknowns, and the ones most likely to belong to a long-missing person are the
sparsest. A scorer that penalises missing data sorts exactly the right
candidates to the bottom. Sparse records stay on the list; a coverage discount
keeps one weak field from outranking broad agreement.

**A hard conflict still excludes.** Wrong sex, or recovered before
`recovery_floor`. That floor is the *earliest* date any source claims, not the
best-supported one, because an over-tight bound throws away real leads.

**The parse fails loudly.** `mshp._rows` verifies the table header before
reading by position. If a column is reordered it raises, instead of comparing
one person's height against another's weight and reporting agreement.

**Never resolved from one source.** `caseIsResolved` and absence from an NCIC
feed both flip for administrative reasons. Two sources must agree, and the
output says to confirm with the agency either way.

## Tests

    pytest tests/

These are the positive controls. A news filter matching nothing and a news
filter that is broken both print "no mentions", so the suite proves the
matcher still accepts a real story about the case while rejecting the
athletes, writers, and obituaries who share the name. It runs entirely
against the synthetic example, and asserts that the example is the only case
file in the repo.

## If you actually have information about a missing person

Call **911** or the investigating agency directly. Do not route a lead to a
medical examiner as a member of the public.

## Licence

MIT.
