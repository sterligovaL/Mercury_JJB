"""Persistent state kept in data/state.json and committed back to the repository."""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

SEEN_RETENTION_DAYS = 90

STATUSES = {
    "new": "новая",
    "interested": "интересно",
    "applied": "откликнулась",
    "rejected": "не подходит",
}


def short_id(key: str) -> str:
    """Compact id that fits into Telegram callback data (64 bytes max)."""
    return hashlib.sha1(key.encode()).hexdigest()[:10]


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.seen: dict[str, str] = data.get("seen", {})
        self.tracker: dict[str, dict] = data.get("tracker", {})
        self.offset: int = data.get("offset", 0)
        self.last_digest: str = data.get("last_digest", "")
        self.force_digest: bool = data.get("force_digest", False)
        self.remotive_date: str = data.get("remotive_date", "")

    def mark_seen(self, key: str, today: date) -> None:
        self.seen[key] = today.isoformat()

    def prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
        self.seen = {k: d for k, d in self.seen.items() if d >= cutoff}
        # Untouched and rejected jobs expire; "interested" and "applied" are kept.
        self.tracker = {
            sid: e for sid, e in self.tracker.items()
            if e["status"] in ("interested", "applied") or e["updated"] >= cutoff
        }

    def track(self, job, today: date) -> str:
        sid = short_id(job.key)
        self.tracker.setdefault(sid, {
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "source": job.source,
            "status": "new",
            "updated": today.isoformat(),
        })
        return sid

    def set_status(self, sid: str, status: str, today: date) -> dict | None:
        entry = self.tracker.get(sid)
        if entry is None or status not in STATUSES:
            return None
        entry["status"] = status
        entry["updated"] = today.isoformat()
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "seen": self.seen,
            "tracker": self.tracker,
            "offset": self.offset,
            "last_digest": self.last_digest,
            "force_digest": self.force_digest,
            "remotive_date": self.remotive_date,
        }
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), "utf-8")
