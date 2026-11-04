"""Keyword filtering and de-duplication."""

from __future__ import annotations

import re

from .sources import Job


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def matches(job: Job, include: list[str], exclude: list[str]) -> bool:
    title = _norm(job.title)
    if any(_norm(word) in title for word in exclude):
        return False
    return any(_norm(word) in title for word in include)


def has_bonus(job: Job, bonus: list[str]) -> bool:
    text = _norm(job.title + " " + job.description)
    return any(_norm(word) in text for word in bonus)


def signature(job: Job) -> str:
    """Board-independent identity: the same vacancy is often posted on several boards."""
    return f"sig:{_norm(job.title)}|{_norm(job.company)}"


def select_new(jobs: list[Job], seen: set[str], filters: dict) -> list[Job]:
    """Return matching jobs not seen before, de-duplicated, Russian-friendly ones first."""
    result: list[Job] = []
    keys: set[str] = set()
    sigs: set[str] = set()
    for job in jobs:
        if job.key in seen or job.key in keys:
            continue
        if not matches(job, filters.get("include", []), filters.get("exclude", [])):
            continue
        sig = signature(job)
        if sig in seen or sig in sigs:
            continue
        keys.add(job.key)
        sigs.add(sig)
        if has_bonus(job, filters.get("bonus", [])):
            job.tags.append("русский")
        result.append(job)
    result.sort(key=lambda j: "русский" not in j.tags)
    return result
