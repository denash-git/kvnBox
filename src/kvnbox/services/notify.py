"""Отправка в Telegram без запущенного бота — для задач cron и алертов."""

from __future__ import annotations

import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from kvnbox.core.models import TelegramSettings

API = "https://api.telegram.org"

# (url, тело, content-type) → ответ Telegram (JSON)
Post = Callable[[str, bytes, str], bytes]


def http_post(url: str, body: bytes, content_type: str) -> bytes:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data: bytes = resp.read()
        return data


class NotifyError(Exception):
    pass


class Telegram:
    def __init__(self, settings: TelegramSettings, post: Post = http_post) -> None:
        self.s = settings
        self.post = post

    @property
    def configured(self) -> bool:
        return bool(self.s.token and self.s.chat_id)

    @property
    def alerts_on(self) -> bool:
        return self.configured and self.s.enabled and self.s.alerts_enabled

    def alert(self, text: str) -> bool:
        """Алерт уходит, только если бот и оповещения включены (как в TransferBox)."""
        if not self.alerts_on:
            return False
        self.send_message(text)
        return True

    def send_message(self, text: str) -> None:
        body = json.dumps({"chat_id": self.s.chat_id, "text": text, "parse_mode": "HTML"})
        self._call("sendMessage", body.encode("utf-8"), "application/json")

    def send_document(self, path: Path, caption: str = "") -> None:
        boundary = "kvnbox" + secrets.token_hex(12)
        fields = {"chat_id": self.s.chat_id, "caption": caption, "parse_mode": "HTML"}
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; '
            f'filename="{path.name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
            + path.read_bytes()
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        self._call("sendDocument", b"".join(parts), f"multipart/form-data; boundary={boundary}")

    def _call(self, method: str, body: bytes, content_type: str) -> None:
        if not self.configured:
            raise NotifyError("Токен или Chat ID Telegram не заданы.")
        try:
            raw = self.post(f"{API}/bot{self.s.token}/{method}", body, content_type)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise NotifyError(f"Ошибка Telegram API (HTTP {e.code}): {detail}") from e
        except Exception as e:
            raise NotifyError(f"Ошибка отправки в Telegram: {e}") from e
        try:
            ok = json.loads(raw).get("ok")
        except (json.JSONDecodeError, AttributeError):
            ok = False
        if not ok:
            raise NotifyError(f"Ошибка Telegram API: {raw[:300]!r}")
