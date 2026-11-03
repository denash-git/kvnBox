"""Ссылки подключения и клиентские конфиги.

Формат совпадает с TransferBox байт-в-байт: у клиентов (в том числе на роутере
Keenetic) ссылки и подписки не должны меняться.
"""

from __future__ import annotations

import json
from urllib.parse import quote

from kvnbox.core.models import (
    Connection,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UuidCredentials,
)

PORT_HTTPS = 443

# Суффикс имени профиля в ссылке. «xhttp» сохранён для 1:1 (D-015).
LINK_SUFFIX = {
    (Protocol.VLESS, Transport.WS): "ws",
    (Protocol.VLESS, Transport.GRPC): "grpc",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "xhttp",
    (Protocol.NAIVE, None): "naive",
    (Protocol.MIERU, None): "mieru",
}


def profile_name(user: User, conn: Connection) -> str:
    return f"{user.name}-{LINK_SUFFIX[conn.slot]}"


def _password_creds(conn: Connection) -> PasswordCredentials:
    if not isinstance(conn.credentials, PasswordCredentials):
        raise TypeError(f"{conn.protocol.value}: ожидались логин и пароль")
    return conn.credentials


def build_link(user: User, conn: Connection, settings: Settings) -> str:
    domain = settings.domain
    name = profile_name(user, conn)

    if conn.protocol is Protocol.NAIVE:
        creds = _password_creds(conn)
        return f"naive://{creds.username}:{creds.password}@{domain}:{PORT_HTTPS}#{name}"

    if conn.protocol is Protocol.VLESS:
        if not isinstance(conn.credentials, UuidCredentials):
            raise TypeError("vless: ожидался UUID")
        base = (
            f"vless://{conn.credentials.uuid}@{domain}:{PORT_HTTPS}"
            f"?encryption=none&security=tls&sni={domain}"
        )
        if conn.transport is Transport.WS:
            return f"{base}&type=ws&path={settings.vless.ws_path}#{name}"
        if conn.transport is Transport.GRPC:
            return f"{base}&type=grpc&serviceName={settings.vless.grpc_service}#{name}"
        return f"{base}&type=httpupgrade&path={settings.vless.httpupgrade_path}#{name}"

    creds = _password_creds(conn)
    return (
        f"mierus://{quote(creds.username)}:{quote(creds.password)}@{domain}"
        f"?profile={quote(name)}&port={settings.mieru.port}"
        f"&protocol=TCP&multiplexing=MULTIPLEXING_HIGH#{name}"
    )


# ─── Mieru: готовые клиентские конфиги ───────────────────────────────────────


def mieru_client_json(user: User, conn: Connection, settings: Settings) -> str:
    creds = _password_creds(conn)
    config = {
        "profileName": profile_name(user, conn),
        "user": {"name": creds.username, "password": creds.password},
        "servers": [
            {
                "ipAddress": settings.domain,
                "portBindings": [{"port": settings.mieru.port, "protocol": "TCP"}],
            }
        ],
        "mtu": 1400,
        "multiplexing": {"level": "MULTIPLEXING_HIGH"},
    }
    return json.dumps(config, indent=2)


def mieru_clash_yaml(user: User, conn: Connection, settings: Settings) -> str:
    creds = _password_creds(conn)
    return (
        "proxies:\n"
        f'  - name: "{profile_name(user, conn)}"\n'
        "    type: mieru\n"
        f"    server: {settings.domain}\n"
        f"    port: {settings.mieru.port}\n"
        f"    username: {creds.username}\n"
        f"    password: {creds.password}\n"
        "    transport: tcp"
    )


def mieru_singbox_json(user: User, conn: Connection, settings: Settings) -> str:
    creds = _password_creds(conn)
    config = {
        "outbounds": [
            {
                "type": "mieru",
                "tag": profile_name(user, conn),
                "server": settings.domain,
                "server_port": settings.mieru.port,
                "transport": "TCP",
                "username": creds.username,
                "password": creds.password,
            }
        ]
    }
    return json.dumps(config, indent=2)
