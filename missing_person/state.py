"""Remember what was already seen, so the watcher only speaks on change.

Gitignored: the state directory holds nothing sensitive, but it is machine
state, not source.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path(__file__).resolve().parent.parent / "state"

# Consecutive misses before a source counts as an outage instead of weather.
# These feeds drop a request now and then: MSHP timed out on the 2026-09-20
# 08:15 run and answered the same URL in 0.45s that evening. A watcher that
# exits non-zero on one miss puts a FAIL in the next session-start report for
# something that already fixed itself, which trains the reader to skip it.
ESCALATE_AFTER = 2


@dataclass
class CaseState:
    case_id: str
    ncic_active: bool | None = None
    namus_resolved: bool | None = None
    namus_modified: str = ""
    seen_articles: list[str] = field(default_factory=list)
    # Name-less local stories already surfaced. Deduped like articles so the
    # first run absorbs the backlog instead of re-notifying it every morning.
    seen_news_candidates: list[str] = field(default_factory=list)
    top_candidates: list[str] = field(default_factory=list)
    # source -> consecutive runs it has failed; reset to 0 on success
    source_failures: dict[str, int] = field(default_factory=dict)
    checked_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return STATE_DIR / f"{self.case_id}.json"

    def note(self, kind: str, detail: str) -> None:
        self.history.append({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "detail": detail,
        })

    def save(self) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = {
            "case_id": self.case_id,
            "ncic_active": self.ncic_active,
            "namus_resolved": self.namus_resolved,
            "namus_modified": self.namus_modified,
            "seen_articles": self.seen_articles[-300:],
            "seen_news_candidates": self.seen_news_candidates[-300:],
            "top_candidates": self.top_candidates,
            "source_failures": self.source_failures,
            "checked_at": self.checked_at,
            "history": self.history[-200:],
        }
        self.path.write_text(json.dumps(payload, indent=2))


def load(case_id: str) -> CaseState:
    path = STATE_DIR / f"{case_id}.json"
    if not path.exists():
        return CaseState(case_id=case_id)
    raw = json.loads(path.read_text())
    return CaseState(
        case_id=raw.get("case_id", case_id),
        ncic_active=raw.get("ncic_active"),
        namus_resolved=raw.get("namus_resolved"),
        namus_modified=raw.get("namus_modified", ""),
        seen_articles=list(raw.get("seen_articles", [])),
        seen_news_candidates=list(raw.get("seen_news_candidates", [])),
        top_candidates=list(raw.get("top_candidates", [])),
        source_failures=dict(raw.get("source_failures", {})),
        checked_at=raw.get("checked_at", ""),
        history=list(raw.get("history", [])),
    )


def escalated(st: CaseState) -> list[str]:
    """Sources that have missed ESCALATE_AFTER runs running.

    The watcher's exit code keys off this, not off whether a single request
    failed, so a red run means a feed is actually down.
    """
    return sorted(s for s, n in st.source_failures.items() if n >= ESCALATE_AFTER)
