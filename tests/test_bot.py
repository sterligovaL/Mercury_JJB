"""Offline tests: source parsers on sample payloads, filtering, state and a full run with a fake Telegram."""

import json
import tempfile
import tomllib
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from bot import main as bot_main
from bot import sources
from bot.filters import select_new
from bot.state import State, short_id

ROOT = Path(__file__).resolve().parent.parent
CFG = tomllib.loads((ROOT / "config.toml").read_text("utf-8"))
TODAY = date(2026, 11, 4)

REMOTIVE = json.dumps({"jobs": [
    {"id": 1, "title": "Project Coordinator", "company_name": "Acme NGO",
     "url": "https://remotive.com/remote-jobs/1", "candidate_required_location": "Europe",
     "description": "<p>Fluent <b>Russian</b> is a plus</p>"},
    {"id": 2, "title": "Senior Software Engineer", "company_name": "Tech",
     "url": "https://remotive.com/remote-jobs/2", "candidate_required_location": "USA", "description": ""},
]}).encode()

WWR = b"""<?xml version="1.0"?><rss><channel>
<item><title>Global Aid: Operations Manager</title><region>Anywhere in the World</region>
<link>https://weworkremotely.com/remote-jobs/global-aid-ops</link>
<guid>https://weworkremotely.com/remote-jobs/global-aid-ops</guid>
<description>&lt;p&gt;Manage operations&lt;/p&gt;</description></item>
<item><title>Acme NGO: Project Coordinator</title><region>Europe</region>
<link>https://weworkremotely.com/remote-jobs/acme-dup</link><guid>wwr-dup</guid>
<description>duplicate of a Remotive post</description></item>
</channel></rss>"""

HH = json.dumps({"items": [
    {"id": "123", "name": "Координатор проектов", "employer": {"name": "ООО Ромашка"},
     "alternate_url": "https://hh.ru/vacancy/123", "area": {"name": "Москва"},
     "schedule": {"name": "Удаленная работа"},
     "snippet": {"requirement": "Опыт координации", "responsibility": None}},
    {"id": "124", "name": "Менеджер по продажам", "employer": {"name": "X"},
     "alternate_url": "https://hh.ru/vacancy/124", "area": {"name": "Москва"}, "snippet": {}},
]}).encode()


class FakeTelegram:
    def __init__(self, updates=None):
        self.sent, self.edited, self.answered = [], [], []
        self._updates = updates or []

    def updates(self, offset):
        return [u for u in self._updates if u["update_id"] >= offset]

    def send(self, chat_id, text, buttons=None):
        self.sent.append((str(chat_id), text, buttons))
        return {}

    def edit_buttons(self, chat_id, message_id, buttons):
        self.edited.append((message_id, buttons))

    def answer_callback(self, cid, text):
        self.answered.append(text)


class ParserTests(unittest.TestCase):
    def test_remotive(self):
        jobs = sources.parse_remotive(REMOTIVE)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].title, "Project Coordinator")
        self.assertIn("Russian", jobs[0].description)
        self.assertNotIn("<b>", jobs[0].description)

    def test_wwr_splits_company_and_title(self):
        jobs = sources.parse_wwr(WWR)
        self.assertEqual((jobs[0].company, jobs[0].title), ("Global Aid", "Operations Manager"))
        self.assertEqual(jobs[0].location, "Anywhere in the World")

    def test_hh(self):
        jobs = sources.parse_hh(HH)
        self.assertEqual(jobs[0].url, "https://hh.ru/vacancy/123")
        self.assertEqual(jobs[0].location, "Москва, Удаленная работа")


class FilterTests(unittest.TestCase):
    def all_jobs(self):
        return sources.parse_remotive(REMOTIVE) + sources.parse_wwr(WWR) + sources.parse_hh(HH)

    def test_selects_matching_and_dedupes(self):
        picked = select_new(self.all_jobs(), set(), CFG["filters"])
        titles = [j.title for j in picked]
        self.assertEqual(titles.count("Project Coordinator"), 1)   # cross-board duplicate removed
        self.assertNotIn("Senior Software Engineer", titles)       # excluded
        self.assertNotIn("Менеджер по продажам", titles)           # no include keyword
        self.assertIn("Координатор проектов", titles)
        self.assertIn("Operations Manager", titles)
        self.assertEqual(picked[0].tags, ["русский"])              # Russian-friendly first

    def test_skips_seen(self):
        jobs = self.all_jobs()
        picked = select_new(jobs, {"hh.ru:123"}, CFG["filters"])
        self.assertNotIn("Координатор проектов", [j.title for j in picked])


class StateTests(unittest.TestCase):
    def test_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            s = State(path)
            job = sources.parse_hh(HH)[0]
            sid = s.track(job, date(2026, 1, 1))
            s.mark_seen(job.key, date(2026, 1, 1))
            other = s.track(sources.parse_remotive(REMOTIVE)[0], date(2026, 1, 1))
            s.set_status(other, "applied", date(2026, 1, 1))
            s.prune(TODAY)  # > 90 days later
            self.assertNotIn(sid, s.tracker)       # untouched job expired
            self.assertIn(other, s.tracker)        # applied job kept
            self.assertEqual(s.seen, {})
            s.save()
            self.assertIn(other, State(path).tracker)

    def test_callback_data_fits_telegram_limit(self):
        data = f"s:interested:{short_id('x' * 500)}"
        self.assertLessEqual(len(data.encode()), 64)


class FullRunTests(unittest.TestCase):
    def run_bot(self, tg, state_path, hour=13, env=None):
        env = {"TELEGRAM_TOKEN": "test", "TELEGRAM_CHAT_ID": "42", **(env or {})}
        fake_now = datetime(2026, 11, 4, hour, 5, tzinfo=bot_main.ZoneInfo("Asia/Jerusalem"))

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_now

        with mock.patch.dict("os.environ", env, clear=True), \
             mock.patch.object(bot_main, "STATE_PATH", state_path), \
             mock.patch.object(bot_main, "Telegram", lambda token: tg), \
             mock.patch.object(bot_main, "datetime", FakeDT), \
             mock.patch.object(sources, "fetch_remotive", lambda cfg: sources.parse_remotive(REMOTIVE)), \
             mock.patch.object(sources, "fetch_wwr", lambda cfg: sources.parse_wwr(WWR)), \
             mock.patch.object(sources, "fetch_hh", side_effect=RuntimeError("HTTP 403")):
            return bot_main.main()

    def test_digest_then_button_then_no_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            tg = FakeTelegram()
            self.assertEqual(self.run_bot(tg, path), 0)
            header = tg.sent[0][1]
            self.assertIn("Новых подходящих вакансий: 2", header)
            self.assertIn("hh.ru: RuntimeError HTTP 403", header)   # failure reported, not fatal
            self.assertIn("linkedin.com", header)
            job_msgs = tg.sent[1:]
            self.assertEqual(len(job_msgs), 2)
            sid = job_msgs[0][2][0][0]["callback_data"].split(":")[2]

            # Press "applied", ask for tracker; digest must not repeat the same day.
            tg2 = FakeTelegram(updates=[
                {"update_id": 10, "callback_query": {"id": "c", "data": f"s:applied:{sid}",
                 "message": {"message_id": 7, "chat": {"id": 42}}}},
                {"update_id": 11, "message": {"text": "/tracker", "chat": {"id": 42}}},
                {"update_id": 12, "message": {"text": "/tracker", "chat": {"id": 999}}},  # stranger
            ])
            self.run_bot(tg2, path, hour=14)
            self.assertEqual(tg2.answered, ["Сохранено: откликнулась"])
            self.assertEqual(tg2.edited[0][1][0][1]["text"], "» Откликнулась")
            self.assertEqual(len(tg2.sent), 1)                     # only tracker, no stranger reply
            self.assertIn("Откликнулась (1)", tg2.sent[0][1])

            # /run forces a new digest; already-sent jobs are not repeated, Remotive not refetched.
            tg3 = FakeTelegram(updates=[{"update_id": 13, "message": {"text": "/run", "chat": {"id": 42}}}])
            self.run_bot(tg3, path, hour=15)
            self.assertIn("Новых подходящих вакансий: 0", tg3.sent[1][1])
            self.assertNotIn("Remotive", tg3.sent[1][1])

    def test_no_digest_before_hour(self):
        with tempfile.TemporaryDirectory() as d:
            tg = FakeTelegram()
            self.run_bot(tg, Path(d) / "state.json", hour=9)
            self.assertEqual(tg.sent, [])

    def test_setup_mode_replies_chat_id(self):
        with tempfile.TemporaryDirectory() as d:
            tg = FakeTelegram(updates=[{"update_id": 1, "message": {"text": "/start", "chat": {"id": 555}}}])
            self.run_bot(tg, Path(d) / "state.json", env={"TELEGRAM_CHAT_ID": ""})
            self.assertEqual(len(tg.sent), 1)
            self.assertIn("555", tg.sent[0][1])


if __name__ == "__main__":
    unittest.main()
