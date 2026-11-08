"""Ссылки подключения и клиентские конфиги.

Формат совпадает с TransferBox байт-в-байт: у клиентов (в том числе на роутере
Keenetic) ссылки и подписки не должны меняться.
"""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import quote

from kvnbox.core.models import (
    Connection,
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UuidCredentials,
)
from kvnbox.cores.xray import CDN_EXTRA, CDN_XMUX

PORT_HTTPS = 443

# Суффикс имени профиля в ссылке (в TransferBox был «xhttp»).
LINK_SUFFIX = {
    (Protocol.VLESS, Transport.WS): "ws",
    (Protocol.VLESS, Transport.GRPC): "grpc",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "httpupgrade",
    (Protocol.VLESS, Transport.XHTTP): "xhttp",
    (Protocol.VLESS, Transport.CDN): "cdn",
    (Protocol.NAIVE, None): "naive",
    (Protocol.MIERU, None): "mieru",
    (Protocol.AWG, None): "awg",
}


def cdn_link(uuid: str, settings: Settings, name: str) -> str:
    """Протокол CDN — как в рабочем образце: адрес, SNI и host = домен CDN,
    параметры XHTTP — в `extra` (JSON, URL-кодирован)."""
    cdn = settings.cdn.domain
    extra = quote(json.dumps({**CDN_EXTRA, "xmux": CDN_XMUX}, separators=(",", ":")), safe="")
    return (
        f"vless://{uuid}@{cdn}:{PORT_HTTPS}?encryption=none&security=tls&sni={cdn}"
        f"&alpn=h2&type=xhttp&mode=packet-up&path={quote(settings.xray.cdn_path, safe='')}"
        f"&host={cdn}&extra={extra}#{name}"
    )


def profile_name(user: User, conn: Connection) -> str:
    return f"{user.name}-{LINK_SUFFIX[conn.slot]}"


def _password_creds(conn: Connection) -> PasswordCredentials:
    if not isinstance(conn.credentials, PasswordCredentials):
        raise TypeError(f"{conn.protocol.value}: ожидались логин и пароль")
    return conn.credentials


def build_link(user: User, conn: Connection, settings: Settings) -> str:
    domain = settings.domain
    name = profile_name(user, conn)

    if conn.protocol is Protocol.AWG:  # ключ приложения AmneziaVPN (в подписки не входит)
        from kvnbox.clients.awg import vpn_key

        return vpn_key(user, conn, settings)

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
        # пути — у каждого ядра свои: по ним Caddy отдаёт запрос нужному ядру (этап 11)
        p = settings.xray if conn.core is CoreKind.XRAY else settings.vless
        if conn.transport is Transport.WS:
            return f"{base}&type=ws&path={p.ws_path}#{name}"
        if conn.transport is Transport.GRPC:
            return f"{base}&type=grpc&serviceName={p.grpc_service}#{name}"
        if conn.transport is Transport.XHTTP:
            return f"{base}&type=xhttp&path={settings.xray.xhttp_path}&mode=auto#{name}"
        if conn.transport is Transport.CDN:
            return cdn_link(conn.credentials.uuid, settings, name)
        return f"{base}&type=httpupgrade&path={p.httpupgrade_path}#{name}"

    creds = _password_creds(conn)
    return (
        f"mierus://{quote(creds.username)}:{quote(creds.password)}@{domain}"
        f"?profile={quote(name)}&port={settings.mieru.port}"
        f"&protocol=TCP&multiplexing=MULTIPLEXING_HIGH#{name}"
    )


# ─── Mieru: готовые клиентские конфиги ───────────────────────────────────────
# Два формата исправлены относительно TransferBox (проверено живым трафиком):
#  * JSON-профиль: домен — в `domainName`; в `ipAddress` официальный клиент
#    mieru 3.39 принимает только IP («failed to parse IP address»);
#  * Clash YAML: `transport: TCP` — mihomo 1.19 не принимает `tcp`.


def _mieru_server_address(host: str) -> dict[str, str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return {"domainName": host}
    return {"ipAddress": host}


def mieru_client_json(user: User, conn: Connection, settings: Settings) -> str:
    creds = _password_creds(conn)
    config = {
        "profileName": profile_name(user, conn),
        "user": {"name": creds.username, "password": creds.password},
        "servers": [
            {
                **_mieru_server_address(settings.domain),
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
        "    transport: TCP"
    )


def mieru_singbox_json(user: User, conn: Connection, settings: Settings) -> str:
    """Outbound `mieru` для клиентов на sing-box с поддержкой Mieru.

    Официальный sing-box (1.14) такого типа не знает — работает только в сборках
    клиентов, где Mieru добавлен.
    """
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
