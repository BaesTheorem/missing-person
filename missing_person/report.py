"""Render a scan into terminal text and an Obsidian case note.

House style for the note: H3/H4 headers, wikilinks, no blank line before a
header. The note is rewritten in full on each scan because it is a projection
of the sources, not a place anyone should hand-edit. Human notes belong in a
separate linked note, which is why the template links out to one.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from missing_person.case import Case
from missing_person.geo import separation, zip_conflicts
from missing_person.match import Candidate
from missing_person.sources.mshp import MissingListing
from missing_person.sources.news import Article, NewsCandidate

VAULT = Path.home() / "Exobrain"
NOTE_DIR = VAULT / "Areas" / "Community" / "Missing Persons"


def status_block(case: Case, ncic: list[MissingListing],
                 namus_resolved: bool | None, namus_modified: str) -> list[str]:
    lines = ["#### Case status"]
    hit = next((m for m in ncic if case.mshp_last.lower() in m.name.lower()
                and case.mshp_first.lower() in m.name.lower()), None)
    if hit:
        lines.append(f"- **NCIC (via MSHP)**: ACTIVE. Listed missing since "
                     f"{hit.missing_since}, {hit.agency}, {hit.missing_from}.")
    else:
        lines.append("- **NCIC (via MSHP)**: **no active listing returned.** "
                     "This can mean the case was cleared, or that MSHP changed its "
                     "feed. Confirm with the agency before concluding anything.")
    if namus_resolved is None:
        lines.append("- **NamUs**: not checked.")
    elif namus_resolved:
        lines.append("- **NamUs**: case marked **RESOLVED**. Confirm with the agency.")
    else:
        lines.append(f"- **NamUs**: open. Record last modified {namus_modified[:10]}.")
    return lines


def render_note(case: Case, ncic: list[MissingListing], namus_resolved: bool | None,
                namus_modified: str, candidates: list[Candidate],
                articles: list[Article], scanned: datetime,
                news_candidates: list[NewsCandidate] | None = None) -> str:
    d = case.description
    out: list[str] = [
        f"# {case.display_name}",
        "",
        f"> [!warning] Active missing-person case. If you see him, call 911. "
        f"Tips to {case.agency}: {case.agency_phones.get('missing_persons', '')}.",
        "",
        f"**Scanned**: {scanned:%Y-%m-%d %H:%M %Z} by the `mp` toolkit",
        "",
        "### Who",
        f"- **Name**: {case.legal_name}"
        + (f" (goes by {', '.join(case.aka)})" if case.aka else ""),
        f"- **Age when missing**: {d.age_at_disappearance}",
        f"- **Description**: {d.race} {d.sex}, "
        f"{d.height_in[0]}-{d.height_in[1]} in, {d.weight_lb[0]}-{d.weight_lb[1]} lb, "
        f"{d.hair} hair, {d.eyes} eyes",
        f"- **Clothing**: {case.clothing or 'unknown'}",
        "",
    ]
    out += status_block(case, ncic, namus_resolved, namus_modified)
    out += ["", "#### Dates each source asserts"]
    for label, value in case.date_claims.items():
        out.append(f"- {label}: {value}")
    out += ["", "#### Last-seen locations, by source"]
    for loc in case.locations:
        out.append(f"- **{loc.label}** ({loc.source}) `{loc.lat:.4f}, {loc.lon:.4f}`"
                   f" (+/- {loc.precision_mi} mi)"
                   + (f" -- {loc.note}" if loc.note else ""))
    for a, b, miles, meaningful in separation(case.locations):
        if meaningful:
            out.append(f"- Separation: {a} to {b} is **{miles} mi**, which exceeds "
                       "the combined positional uncertainty. A real disagreement.")
        else:
            out.append(f"- Separation: {a} to {b} measures {miles} mi, which is "
                       "**inside the combined positional uncertainty**. This "
                       "measurement cannot tell you whether they disagree.")
    for a_label, a_zip, b_label, b_zip in zip_conflicts(case.locations):
        out.append(f"- **ZIP conflict**: {a_label} is in {a_zip}, {b_label} is in "
                   f"{b_zip}. Different ZIPs for the same reported moment is a "
                   "real disagreement, at a resolution the coordinates lack.")

    out += ["", "#### Unidentified-person candidates",
            "These are RANKED LEADS, not identifications. Only the medical examiner "
            "and the investigating agency can identify anyone. Send a lead to the "
            "agency; do not contact a county ME directly as a member of the public."]
    if candidates:
        out.append("")
        out.append("| Score | NamUs | Found | Where | Est. age | Ht | Wt | Matched |")
        out.append("|---|---|---|---|---|---|---|---|")
        for c in candidates[:20]:
            r = c.record
            ht = f"{r.height_in[0]}-{r.height_in[1]}" if r.height_in else "?"
            wt = f"{r.weight_lb[0]}-{r.weight_lb[1]}" if r.weight_lb else "?"
            age = f"{r.age_est[0]}-{r.age_est[1]}" if r.age_est else "?"
            where = ", ".join(x for x in (r.county, r.state) if x) or "?"
            out.append(f"| {c.score:.2f} | [{r.case_id}]({r.url}) | "
                       f"{r.date_found or '?'} | {where} | {age} | {ht} | {wt} | "
                       f"{', '.join(c.matched) or '-'} |")
    else:
        out.append("- No unidentified-person record in the searched states is "
                   "consistent with the description. That is the good outcome for "
                   "this check, and it is only as current as NamUs.")

    out += ["", "#### News mentions"]
    if articles:
        for a in articles[:15]:
            out.append(f"- [{a.title}]({a.url}) -- {a.source}, {a.published}")
    else:
        out.append("- No story in the watched feeds printed the name. That is not "
                   "the same as no coverage: most local headlines about a missing "
                   "person never print it. See the candidates below.")

    out += ["", "#### Possible coverage, name not printed",
            "Local missing-person stories whose headline omits the name, listed "
            "with the case facts they agree with. UNVERIFIED. Read the story "
            "before treating any of these as being about this case."]
    if news_candidates:
        out.append("")
        out.append("| | Story | Agrees with | Conflicts |")
        out.append("|---|---|---|---|")
        for c in news_candidates[:15]:
            a = c.article
            mark = "**strong**" if c.strong else "weak"
            out.append(f"| {mark} | [{a.title}]({a.url}) -- {a.source} | "
                       f"{', '.join(c.corroborates) or '-'} | "
                       f"{', '.join(c.contradicts) or '-'} |")
    else:
        out.append("- None. No unnamed local missing-person story matched any "
                   "case fact.")

    out += ["", "#### Links"]
    for label, url in case.links.items():
        out.append(f"- [{label}]({url})")
    out += ["", "---", "", "Human notes, tips, and family contact go in a separate note. "
            "This one is regenerated on every scan and hand edits will be lost."]
    return "\n".join(out) + "\n"


def write_note(case: Case, body: str) -> Path:
    NOTE_DIR.mkdir(parents=True, exist_ok=True)
    path = NOTE_DIR / f"{case.display_name}.md"
    path.write_text(body)
    return path


def terminal(case: Case, ncic: list[MissingListing], namus_resolved: bool | None,
             candidates: list[Candidate], articles: list[Article],
             news_candidates: list[NewsCandidate] | None = None) -> str:
    lines = [f"== {case.display_name} ==",
             f"   {case.agency} | tips {case.agency_phones.get('missing_persons', '')}"]
    active = any(case.mshp_last.lower() in m.name.lower() for m in ncic)
    lines.append(f"   NCIC active listing : {'YES' if active else 'NO -- investigate'}")
    lines.append(f"   NamUs resolved flag : {namus_resolved}")
    lines.append(f"   UP candidates       : {len(candidates)}")
    for c in candidates[:8]:
        r = c.record
        lines.append(f"     {c.score:.2f} {r.case_id:<10} {str(r.date_found or '?'):<11}"
                     f" {r.county} {r.state} ({', '.join(c.matched) or 'weak'})")
    lines.append(f"   news mentions       : {len(articles)}")
    for a in articles[:5]:
        lines.append(f"     {a.source}: {a.title[:70]}")
    strong = [c for c in (news_candidates or []) if c.strong]
    lines.append(f"   possible coverage   : {len(strong)} strong"
                 f" / {len(news_candidates or [])} total (name not printed)")
    for c in strong[:5]:
        lines.append(f"     [{', '.join(c.corroborates)}] {c.article.title[:60]}")
    return "\n".join(lines)


def days_missing(case: Case, today: date | None = None) -> int:
    return ((today or date.today()) - case.last_seen_date).days
