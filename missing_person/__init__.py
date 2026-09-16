"""Toolkit for assisting a public missing-person search.

The sources wired up here are government databases, newsroom feeds and open
map data. That is what has been useful so far, not a boundary: which record
to pull next is a judgement call about the case in front of you, and a fixed
list of forbidden sources would mostly serve to miss the lead that mattered.

The INVARIANTS below are different in kind. They are about not stating things
that are untrue, which stays binding no matter how the search widens.

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
