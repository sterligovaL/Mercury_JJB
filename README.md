# Job digest bot

Telegram bot that sends a daily digest of new vacancies and keeps a simple application tracker.
Runs for free on GitHub Actions every 30 minutes; no server needed.

## What it does

- Once a day (first run at or after `digest_hour`, default 13:00 Asia/Jerusalem) it fetches:
  - **Remotive** (public API, fetched at most once a day per its terms; links back to Remotive)
  - **We Work Remotely** (public RSS feeds)
  - **hh.ru** (public vacancy search API)
- Keeps vacancies whose title matches `filters.include` and not `filters.exclude` (`config.toml`).
- Removes duplicates (same id, or same title + company on another board) and never repeats a vacancy.
- Vacancies mentioning Russian are marked and shown first.
- Adds links to saved **LinkedIn** searches (LinkedIn forbids automated collection, so nothing is scraped).
- Each vacancy has buttons: *Интересно / Откликнулась / Не подходит*. Presses are processed on the
  next run (within ~30 minutes).

Commands: `/tracker` (interested and applied jobs), `/run` (extra digest on the next run), `/help`.

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`) and copy the token.
2. Create a **private** GitHub repository and upload these files.
3. Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_TOKEN` — the token from step 1.
4. Actions → job-bot → Run workflow. Send `/start` to your bot, run the workflow again:
   the bot replies with your chat id.
5. Add secret `TELEGRAM_CHAT_ID` with that number. Done.

Optional secrets: `HH_USER_AGENT` (hh.ru asks clients to identify themselves, e.g.
`MyJobBot/1.0 (you@example.com)`), `HH_TOKEN` (hh.ru application token from dev.hh.ru, only if
hh.ru starts rejecting anonymous search).

## Where data lives

`data/state.json` is stored on the `bot-state` branch (never on `main`): sent vacancy ids,
tracker statuses, Telegram offset. Untouched and rejected vacancies expire after 90 days;
"interested" and "applied" are kept.

## Changing the search

Edit `config.toml`: keywords, hh.ru queries, WWR feeds, LinkedIn links, digest hour, max jobs per digest.
Adding a new source = a `fetch_*`/`parse_*` pair in `bot/sources.py` and one line in `collect()` in `bot/main.py`.

## Tests

```
python -m unittest -v
```

Offline tests cover parsers on sample payloads, filtering, de-duplication, state, a full run
with a fake Telegram (digest, button press, tracker, `/run`, setup mode).
`scripts/state.sh` was checked against a local git remote (restore, save, no-op when unchanged).
