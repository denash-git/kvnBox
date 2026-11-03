"""Конфиг sing-box: socks-upstream для NaiveProxy и VLESS inbound'ы за Caddy."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from kvnbox.core.models import CoreKind, Settings, Transport, User, UuidCredentials
from kvnbox.core.paths import Paths
from kvnbox.cores import ports
from kvnbox.cores.base import RenderedFile, active_connections

# Теги inbound'ов как в TransferBox («xhttp» — исторически, это HTTPUpgrade, D-015)
INBOUND_TAGS = {
    Transport.WS: "vless-ws-in",
    Transport.GRPC: "vless-grpc-in",
    Transport.HTTPUPGRADE: "vless-xhttp-in",
}


def vless_users(users: Sequence[User], transport: Transport) -> list[dict[str, str]]:
    return [
        {"uuid": c.credentials.uuid, "name": f"{u.name}@vless"}
        for u, c in active_connections(users, CoreKind.SINGBOX)
        if c.transport is transport and isinstance(c.credentials, UuidCredentials)
    ]


def _transport(settings: Settings, transport: Transport) -> dict[str, str]:
    if transport is Transport.WS:
        return {"type": "ws", "path": settings.vless.ws_path}
    if transport is Transport.GRPC:
        return {"type": "grpc", "service_name": settings.vless.grpc_service}
    return {
        "type": "httpupgrade",
        "host": settings.domain,
        "path": settings.vless.httpupgrade_path,
    }


def build_config(settings: Settings, users: Sequence[User]) -> dict[str, Any]:
    inbounds: list[dict[str, Any]] = [
        {
            "type": "socks",
            "tag": "naive-upstream-in",
            "listen": ports.LOCALHOST,
            "listen_port": ports.NAIVE_UPSTREAM,
            "users": [],
        }
    ]
    for transport in (Transport.WS, Transport.GRPC, Transport.HTTPUPGRADE):
        inbounds.append(
            {
                "type": "vless",
                "tag": INBOUND_TAGS[transport],
                "listen": ports.LOCALHOST,
                "listen_port": ports.VLESS[transport],
                "users": vless_users(users, transport),
                "transport": _transport(settings, transport),
            }
        )
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "cloudflare-dns", "type": "https", "server": "1.1.1.1"},
                {"tag": "local-dns", "type": "local"},
            ]
        },
        "inbounds": inbounds,
        "outbounds": [
            {"type": "direct", "tag": "direct-out"},
            {"type": "block", "tag": "block-out"},
        ],
        "route": {
            "default_domain_resolver": "cloudflare-dns",
            "rules": [{"protocol": "dns", "action": "hijack-dns"}],
            "auto_detect_interface": True,
        },
    }


def render(settings: Settings, users: Sequence[User], paths: Paths) -> list[RenderedFile]:
    content = json.dumps(build_config(settings, users), indent=2, ensure_ascii=False) + "\n"
    return [RenderedFile(paths.singbox_config, content, 0o640, owner="root", group="sing-box")]
