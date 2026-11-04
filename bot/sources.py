"""Job sources. Each fetcher returns a list of Job; parsers are separated from HTTP for testing."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

USER_AGENT = "job-digest-bot/1.0"
TIMEOUT = 30


@dataclass
class Job:
    source: str
    source_id: str
    title: str
    company: str
    url: str
    location: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"


def _get(url: str, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or "")).strip()


# --- Remotive -------------------------------------------------------------

REMOTIVE_URL = "https://remotive.com/api/remote-jobs"


def parse_remotive(payload: bytes) -> list[Job]:
    data = json.loads(payload)
    jobs = []
    for item in data.get("jobs", []):
        jobs.append(Job(
            source="Remotive",
            source_id=str(item["id"]),
            title=item.get("title", "").strip(),
            company=item.get("company_name", "").strip(),
            url=item.get("url", ""),
            location=item.get("candidate_required_location", "") or "Remote",
            description=_strip_html(item.get("description", ""))[:3000],
        ))
    return jobs


def fetch_remotive(cfg: dict) -> list[Job]:
    return parse_remotive(_get(REMOTIVE_URL))


# --- We Work Remotely (RSS) ----------------------------------------------

def parse_wwr(payload: bytes) -> list[Job]:
    root = ET.fromstring(payload)
    jobs = []
    for item in root.iter("item"):
        raw_title = (item.findtext("title") or "").strip()
        # WWR titles look like "Company: Job title"
        company, _, title = raw_title.partition(": ")
        if not title:
            company, title = "", raw_title
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        jobs.append(Job(
            source="We Work Remotely",
            source_id=guid,
            title=title.strip(),
            company=company.strip(),
            url=link,
            location=(item.findtext("region") or "Remote").strip(),
            description=_strip_html(item.findtext("description") or "")[:3000],
        ))
    return jobs


def fetch_wwr(cfg: dict) -> list[Job]:
    jobs: list[Job] = []
    for feed in cfg.get("feeds", []):
        jobs.extend(parse_wwr(_get(feed)))
    return jobs


# --- hh.ru ----------------------------------------------------------------

HH_URL = "https://api.hh.ru/vacancies"


def parse_hh(payload: bytes) -> list[Job]:
    data = json.loads(payload)
    jobs = []
    for item in data.get("items", []):
        snippet = item.get("snippet") or {}
        desc = " ".join(filter(None, [snippet.get("requirement"), snippet.get("responsibility")]))
        schedule = (item.get("schedule") or {}).get("name", "")
        area = (item.get("area") or {}).get("name", "")
        jobs.append(Job(
            source="hh.ru",
            source_id=str(item["id"]),
            title=item.get("name", "").strip(),
            company=((item.get("employer") or {}).get("name") or "").strip(),
            url=item.get("alternate_url", ""),
            location=", ".join(filter(None, [area, schedule])),
            description=_strip_html(desc),
        ))
    return jobs


def fetch_hh(cfg: dict, user_agent: str, token: str | None = None) -> list[Job]:
    jobs: list[Job] = []
    headers = {"HH-User-Agent": user_agent, "User-Agent": user_agent}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for query in cfg.get("queries", []):
        params = {"text": query, "search_field": "name", "period": 1, "per_page": 50}
        if cfg.get("remote_only"):
            params["schedule"] = "remote"
        url = f"{HH_URL}?{urllib.parse.urlencode(params)}"
        jobs.extend(parse_hh(_get(url, headers)))
    return jobs
