"""Конфиг mita (сервер Mieru) и systemd override, указывающий на JSON-конфиг.

Mieru слушает свой порт (TCP+UDP) напрямую, не через Caddy. Пустой список
пользователей — штатное состояние (модуль включён, подключений Mieru нет).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from kvnbox.core.models import CoreKind, PasswordCredentials, Settings, User
from kvnbox.core.paths import Paths
from kvnbox.cores.base import RenderedFile, active_connections

MTU = 1400
CONFIG_PATH_ON_SERVER = "/etc/mita/server.conf.json"


def build_config(settings: Settings, users: Sequence[User]) -> dict[str, Any]:
    port = settings.mieru.port
    port_range = f"{port}-{port}"
    return {
        "portBindings": [
            {"portRange": port_range, "protocol": "TCP"},
            {"portRange": port_range, "protocol": "UDP"},
        ],
        "users": [
            {"name": c.credentials.username, "password": c.credentials.password}
            for _, c in active_connections(users, CoreKind.MITA)
            if isinstance(c.credentials, PasswordCredentials)
        ],
        "loggingLevel": "INFO",
        "mtu": MTU,
    }


def service_override() -> str:
    return f'[Service]\nEnvironment="MITA_CONFIG_JSON_FILE={CONFIG_PATH_ON_SERVER}"\n'


def render(settings: Settings, users: Sequence[User], paths: Paths) -> list[RenderedFile]:
    """Файлы mita. Пусто, если модуль Mieru выключен (конфиг не трогаем, как в TransferBox)."""
    if not settings.mieru.enabled:
        return []
    return [
        RenderedFile(paths.mita_service_override, service_override(), 0o600),
        RenderedFile(
            paths.mita_config,
            json.dumps(build_config(settings, users), indent=2),
            0o600,
            owner="mita",
            group="mita",
        ),
    ]
