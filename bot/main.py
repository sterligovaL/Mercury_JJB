"""Entry point: process Telegram button presses/commands, then send the daily digest when due.

Run every ~30 minutes (GitHub Actions). Environment variables:
  TELEGRAM_TOKEN    bot token from @BotFather (required)
  TELEGRAM_CHAT_ID  your chat id (if missing, the bot replies to /start with it)
  HH_USER_AGENT     optional, app name for hh.ru, e.g. "MyJobBot/1.0 (you@example.com)"
  HH_TOKEN          optional hh.ru application token
"""

from __future__ import annotations

import html
import os
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import sources
from .filters import select_new, signature
from .state import STATUSES, State
from .telegram import Telegram

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
STATE_PATH = ROOT / "data" / "state.json"

HELP = (
    "Бот поиска работы.\n"
    "Подборка приходит раз в день. Кнопки под вакансией сохраняют статус "
    "(срабатывают в течение ~30 минут).\n\n"
    "/tracker — вакансии со статусом «интересно» и «откликнулась»\n"
    "/run — прислать подборку вне расписания (при следующем запуске)"
)


def esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def job_buttons(sid: str, current: str = "new") -> list[list[dict]]:
    row = []
    for status in ("interested", "applied", "rejected"):
        label = STATUSES[status].capitalize()
        if status == current:
            label = f"» {label}"
        row.append({"text": label, "callback_data": f"s:{status}:{sid}"})
    return [row]


def format_job(job) -> str:
    lines = [f"<b>{esc(job.title)}</b>"]
    meta = " · ".join(filter(None, [job.company, job.location]))
    if meta:
        lines.append(esc(meta))
    tag = f"{job.source}" + (" · упоминается русский" if "русский" in job.tags else "")
    lines.append(esc(tag))
    lines.append(f'<a href="{esc(job.url)}">Открыть вакансию</a>')
    return "\n".join(lines)


def format_tracker(state: State) -> list[str]:
    parts = []
    for status in ("applied", "interested"):
        entries = [e for e in state.tracker.values() if e["status"] == status]
        if not entries:
            continue
        entries.sort(key=lambda e: e["updated"], reverse=True)
        parts.append(f"<b>{STATUSES[status].capitalize()} ({len(entries)})</b>")
        for e in entries:
            parts.append(f'• <a href="{esc(e["url"])}">{esc(e["title"])}</a> — '
                         f'{esc(e["company"])} ({e["updated"]})')
        parts.append("")
    if not parts:
        return ["Трекер пуст: отметьте вакансии кнопками в подборке."]
    # Telegram limit is 4096 chars per message.
    chunks, current = [], ""
    for line in parts:
        if len(current) + len(line) + 1 > 3800:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def handle_updates(tg: Telegram, state: State, chat_id: str | None, today) -> None:
    for update in tg.updates(state.offset):
        state.offset = update["update_id"] + 1

        if "callback_query" in update:
            cq = update["callback_query"]
            msg = cq.get("message") or {}
            if str((msg.get("chat") or {}).get("id")) != str(chat_id):
                continue
            kind, _, rest = (cq.get("data") or "").partition(":")
            status, _, sid = rest.partition(":")
            entry = state.set_status(sid, status, today) if kind == "s" else None
            if entry is None:
                tg.answer_callback(cq["id"], "Вакансия не найдена")
                continue
            tg.answer_callback(cq["id"], f"Сохранено: {STATUSES[status]}")
            try:
                tg.edit_buttons(chat_id, msg["message_id"], job_buttons(sid, status))
            except RuntimeError as err:
                print(f"warning: could not update buttons: {err}")
            continue

        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        msg_chat = str((message.get("chat") or {}).get("id", ""))
        if not chat_id:
            if text.startswith("/start"):
                tg.send(msg_chat, f"Ваш chat id: {msg_chat}\nДобавьте его в секрет TELEGRAM_CHAT_ID.")
            continue
        if msg_chat != str(chat_id):
            continue  # ignore strangers
        command = text.split()[0].split("@")[0] if text else ""
        if command in ("/start", "/help"):
            tg.send(chat_id, HELP)
        elif command == "/tracker":
            for chunk in format_tracker(state):
                tg.send(chat_id, chunk)
        elif command == "/run":
            state.force_digest = True
            tg.send(chat_id, "Собираю подборку.")


def collect(cfg: dict, state: State, today) -> tuple[list, list[str], list[str]]:
    src = cfg.get("sources", {})
    jobs, ok, failed = [], [], []
    hh_ua = os.environ.get("HH_USER_AGENT") or "JobDigestBot/1.0 (personal job search)"

    plan = []
    if src.get("remotive", {}).get("enabled"):
        # Respect Remotive's request limit: at most one fetch per day.
        if state.remotive_date != today.isoformat():
            plan.append(("Remotive", lambda: sources.fetch_remotive(src["remotive"])))
    if src.get("weworkremotely", {}).get("enabled"):
        plan.append(("We Work Remotely", lambda: sources.fetch_wwr(src["weworkremotely"])))
    if src.get("hh", {}).get("enabled"):
        plan.append(("hh.ru", lambda: sources.fetch_hh(src["hh"], hh_ua, os.environ.get("HH_TOKEN"))))

    for name, fetch in plan:
        try:
            found = fetch()
            jobs.extend(found)
            ok.append(f"{name}: {len(found)}")
            if name == "Remotive":
                state.remotive_date = today.isoformat()
        except Exception as err:  # one broken source must not stop the digest
            failed.append(f"{name}: {type(err).__name__} {err}"[:200])
    return jobs, ok, failed


def send_digest(tg: Telegram, cfg: dict, state: State, chat_id: str, today) -> None:
    jobs, ok, failed = collect(cfg, state, today)
    new_jobs = select_new(jobs, set(state.seen), cfg.get("filters", {}))
    limit = cfg.get("schedule", {}).get("max_jobs_per_digest", 15)
    to_send = new_jobs[:limit]

    header = [f"<b>Подборка {today.strftime('%d.%m.%Y')}</b>",
              f"Новых подходящих вакансий: {len(new_jobs)}"
              + (f" (показаны {len(to_send)}, остальные придут завтра)" if len(new_jobs) > limit else "")]
    if ok:
        header.append("Проверено: " + ", ".join(ok))
    if failed:
        header.append("Не удалось получить: " + esc("; ".join(failed)))
    links = cfg.get("linkedin", {}).get("searches", [])
    if links:
        header.append("\nLinkedIn (открыть вручную):")
        header += [f'• <a href="{esc(s["url"])}">{esc(s["name"])}</a>' for s in links]
    tg.send(chat_id, "\n".join(header))

    for job in to_send:
        sid = state.track(job, today)
        tg.send(chat_id, format_job(job), job_buttons(sid))
        state.mark_seen(job.key, today)
        state.mark_seen(signature(job), today)


def main() -> int:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("error: TELEGRAM_TOKEN is not set")
        return 1
    chat_id = os.environ.get("TELEGRAM_CHAT_ID") or None
    cfg = tomllib.loads(CONFIG_PATH.read_text("utf-8"))
    tz = ZoneInfo(cfg.get("schedule", {}).get("timezone", "Asia/Jerusalem"))
    now = datetime.now(tz)
    today = now.date()

    tg = Telegram(token)
    state = State(STATE_PATH)
    try:
        handle_updates(tg, state, chat_id, today)
        if chat_id:
            due = now.hour >= cfg.get("schedule", {}).get("digest_hour", 13) \
                and state.last_digest != today.isoformat()
            if due or state.force_digest:
                send_digest(tg, cfg, state, chat_id, today)
                if due:
                    state.last_digest = today.isoformat()
                state.force_digest = False
        state.prune(today)
    finally:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
