"""Подписки: base64 от списка ссылок включённых подключений пользователя.

Файл называется токеном подписки и раздаётся Caddy по `/sub/<token>`.
Пользователь без включённых подключений подписки не получает.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

from kvnbox.clients.links import build_link
from kvnbox.core.models import Protocol, Settings, User


def subscription_body(user: User, settings: Settings) -> str | None:
    # AmneziaWG — не для клиентов подписок (sing-box/Xray его не понимают): у него свой конфиг
    links = [
        build_link(user, c, settings)
        for c in user.connections
        if c.enabled and c.protocol is not Protocol.AWG
    ]
    if not links:
        return None
    return base64.b64encode("\n".join(links).encode("utf-8")).decode("ascii")


def build_subscriptions(users: Sequence[User], settings: Settings) -> dict[str, str]:
    """Токен → содержимое файла подписки."""
    result: dict[str, str] = {}
    for user in users:
        body = subscription_body(user, settings)
        if body is not None:
            result[user.sub_token] = body
    return result
