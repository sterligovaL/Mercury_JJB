"""Minimal Telegram Bot API client (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, **params) -> dict:
        body = json.dumps(params).encode()
        req = urllib.request.Request(
            API.format(token=self.token, method=method),
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            # Telegram returns 4xx with a JSON body explaining the problem.
            data = json.loads(err.read() or b"{}")
        if not data.get("ok"):
            # Never include the token in error messages.
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def send(self, chat_id: str, text: str, buttons: list[list[dict]] | None = None) -> dict:
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        return self.call("sendMessage", **params)

    def edit_buttons(self, chat_id, message_id: int, buttons: list[list[dict]]) -> None:
        self.call("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
                  reply_markup={"inline_keyboard": buttons})

    def answer_callback(self, callback_id: str, text: str) -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except RuntimeError:
            # Callbacks older than a few minutes can no longer be answered; that's expected.
            pass

    def updates(self, offset: int) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=0,
                         allowed_updates=["message", "callback_query"])
