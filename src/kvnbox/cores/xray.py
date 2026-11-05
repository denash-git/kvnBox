"""Конфиг Xray (этап 11): VLESS inbound'ы за Caddy — WS, gRPC, HTTPUpgrade, XHTTP.

Xray слушает только 127.0.0.1; TLS снимает Caddy и по пути отдаёт запрос нужному
ядру. Файл создаётся, только когда Xray установлен (пути заданы, D-042).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from kvnbox.core.models import CoreKind, Settings, Transport, User, UuidCredentials
from kvnbox.core.paths import Paths
from kvnbox.cores import ports
from kvnbox.cores.base import RenderedFile, active_connections

TRANSPORTS = (Transport.WS, Transport.GRPC, Transport.HTTPUPGRADE, Transport.XHTTP)
SERVICE_USER = "xray"

# Протокол CDN (XHTTP через Яндекс CDN): параметры из образца владельца, без изменений.
# Данные клиента — в заголовке X-Data обычных GET, маскировка padding. Порядок ключей —
# как в образце (попадает в `extra` ссылки). Замеры: PLAN-stage11 §11.
CDN_EXTRA: dict[str, str | bool] = {
    "mode": "packet-up",
    "scMaxEachPostBytes": "3500",
    "scMinPostsIntervalMs": "0",
    "uplinkChunkSize": "4000",
    "uplinkDataKey": "X-Data",
    "uplinkDataPlacement": "header",
    "uplinkHTTPMethod": "GET",
    "xPaddingBytes": "100-500",
    "xPaddingHeader": "X-Cache",
    "xPaddingKey": "_dc",
    "xPaddingMethod": "tokenish",
    "xPaddingObfsMode": True,
    "xPaddingPlacement": "queryInHeader",
}
CDN_XMUX = {"maxConcurrency": 16, "cMaxReuseTimes": 64, "hKeepAlivePeriod": 60}


def xray_running(settings: Settings, users: Sequence[User]) -> bool:
    """Нужна ли служба: Xray включён в меню и есть хотя бы одно включённое подключение."""
    return (
        settings.xray.enabled
        and settings.xray.configured
        and any(True for _ in active_connections(users, CoreKind.XRAY))
    )


def clients(users: Sequence[User], transport: Transport) -> list[dict[str, str]]:
    return [
        {"id": c.credentials.uuid, "email": f"{u.name}@{transport.value}"}
        for u, c in active_connections(users, CoreKind.XRAY)
        if c.transport is transport and isinstance(c.credentials, UuidCredentials)
    ]


def stream_settings(settings: Settings, transport: Transport) -> dict[str, Any]:
    x = settings.xray
    if transport is Transport.WS:
        return {"network": "ws", "wsSettings": {"path": x.ws_path}}
    if transport is Transport.GRPC:
        return {"network": "grpc", "grpcSettings": {"serviceName": x.grpc_service}}
    if transport is Transport.HTTPUPGRADE:
        return {
            "network": "httpupgrade",
            "httpupgradeSettings": {"path": x.httpupgrade_path, "host": settings.domain},
        }
    if transport is Transport.CDN:
        # данные в заголовках возможны только в packet-up (иначе Xray не запускается)
        return {"network": "xhttp", "xhttpSettings": {"path": x.cdn_path, **CDN_EXTRA}}
    return {"network": "xhttp", "xhttpSettings": {"path": x.xhttp_path, "mode": "auto"}}


def cdn_active(settings: Settings, users: Sequence[User]) -> bool:
    """Нужен ли вход CDN: CDN настроен, путь есть и есть включённое подключение."""
    return (
        settings.cdn.configured
        and bool(settings.xray.cdn_path)
        and any(c.transport is Transport.CDN for _, c in active_connections(users, CoreKind.XRAY))
    )


def build_config(settings: Settings, users: Sequence[User]) -> dict[str, Any]:
    transports = TRANSPORTS + ((Transport.CDN,) if cdn_active(settings, users) else ())
    inbounds = [
        {
            "tag": f"vless-{t.value}-in",
            "listen": ports.LOCALHOST,
            "port": ports.XRAY[t],
            "protocol": "vless",
            "settings": {"clients": clients(users, t), "decryption": "none"},
            "streamSettings": stream_settings(settings, t),
        }
        for t in transports
    ]
    return {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
    }


def render(settings: Settings, users: Sequence[User], paths: Paths) -> list[RenderedFile]:
    if not settings.xray.configured:
        return []  # Xray не установлен — файла нет
    content = json.dumps(build_config(settings, users), indent=2, ensure_ascii=False) + "\n"
    return [RenderedFile(paths.xray_config, content, 0o640, owner="root", group=SERVICE_USER)]
