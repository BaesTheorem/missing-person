"""Toolkit for assisting a public missing-person search.

Scope is deliberately narrow: PUBLIC records and PUBLIC safety channels only.
Every source here is either a government database published for exactly this
purpose (NCIC via MSHP, NamUs), a newsroom's own feed, or open map data. The
toolkit does not touch private individuals' accounts, phone data, or anything
that is not already published for the public to help with.

INVARIANTS
  - Sources are read-only. Nothing here files, edits, or closes a case; only
    the investigating agency can do that.
  - A case is never reported "resolved" from a single source. `caseIsResolved`
    in NamUs and absence from the NCIC feed have to agree, because either one
    alone flips for administrative reasons that have nothing to do with the
    person being found.
  - Unidentified-person matches are RANKED CANDIDATES, never identifications.
    Rendering must keep the word "candidate" attached to every one of them.
"""

# No __all__ and no eager submodule imports: `mp status` should not pay for
# the Overpass and feed-parsing modules it never touches.
