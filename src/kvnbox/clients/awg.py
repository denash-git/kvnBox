"""Клиентские конфиги AmneziaWG 3.1: `.conf` (AmneziaWG/AmneziaVPN, QR-код) и ключ
`vpn://` приложения AmneziaVPN.

Формат ключа — по исходникам amnezia-client (exportController.cpp, importController.cpp,
awgProtocolConfig.cpp): `"vpn://" + base64url(qCompress(json))` без «=»; qCompress —
4 байта длины (big-endian) + поток zlib.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib

from kvnbox.core.models import AWG_SUBNET, AwgCredentials, Connection, Settings, User
from kvnbox.system import awg

DNS = ("1.1.1.1", "1.0.0.1")
ALLOWED_IPS = ("0.0.0.0/0", "::/0")
KEEPALIVE = 25


def _creds(conn: Connection) -> AwgCredentials:
    if not isinstance(conn.credentials, AwgCredentials):
        raise TypeError("awg: ожидались ключи AmneziaWG")
    return conn.credentials


def client_conf(user: User, conn: Connection, settings: Settings) -> str:
    """Конфиг клиента: всё в [Interface] (как читает amneziawg-tools 3.1), сервер — [Peer]."""
    c = _creds(conn)
    s = settings.awg
    lines = [
        "[Interface]",
        f"PrivateKey = {c.private_key}",
        f"Address = {c.address}/32",
        f"DNS = {', '.join(DNS)}",
        f"MTU = {awg.MTU}",
    ]
    lines += [f"{k} = {v}" for k, v in awg.client_params(s).items()]
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {awg.public_key(s.private_key)}",
        f"PresharedKey = {c.preshared_key}",
        f"AllowedIPs = {', '.join(ALLOWED_IPS)}",
        f"Endpoint = {settings.domain}:{s.port}",
        f"PersistentKeepalive = {KEEPALIVE}",
    ]
    return "\n".join(lines) + "\n"


def file_name(user: User) -> str:
    return f"{user.name}-amneziawg.conf"


def _q_compress(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + zlib.compress(data, 8)


def vpn_key(user: User, conn: Connection, settings: Settings) -> str:
    """Ключ для AmneziaVPN («Вставить ключ» / QR): один контейнер amnezia-awg."""
    c = _creds(conn)
    s = settings.awg
    params = awg.client_params(s)
    client_pub = awg.public_key(c.private_key)
    last_config = {
        "config": client_conf(user, conn, settings),
        "hostName": settings.domain,
        "port": s.port,
        "client_ip": c.address,
        "client_priv_key": c.private_key,
        "client_pub_key": client_pub,
        "server_pub_key": awg.public_key(s.private_key),
        "psk_key": c.preshared_key,
        "client_id": client_pub,
        "allowed_ips": list(ALLOWED_IPS),
        "persistent_keep_alive": str(KEEPALIVE),
        "mtu": str(awg.MTU),
        **params,
    }
    subnet, cidr = AWG_SUBNET.split("/")
    awg_obj = {
        "port": str(s.port),
        "transport_proto": "udp",
        "protocol_version": "3.1",
        "subnet_address": subnet,
        "subnet_cidr": cidr,
        **params,
        "last_config": json.dumps(last_config, separators=(",", ":"), ensure_ascii=False),
    }
    doc = {
        "containers": [{"container": "amnezia-awg", "awg": awg_obj}],
        "defaultContainer": "amnezia-awg",
        "description": settings.domain,
        "dns1": DNS[0],
        "dns2": DNS[1],
        "hostName": settings.domain,
        "format_version": 1,
    }
    raw = json.dumps(doc, indent=4, ensure_ascii=False).encode()
    return "vpn://" + base64.urlsafe_b64encode(_q_compress(raw)).decode().rstrip("=")


def decode_vpn_key(key: str) -> dict[str, object]:
    """Обратное преобразование (проверка, тесты)."""
    b64 = key.removeprefix("vpn://")
    data = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    (length,) = struct.unpack(">I", data[:4])
    raw = zlib.decompress(data[4:])
    if len(raw) != length:
        raise ValueError("длина не совпадает")
    result: dict[str, object] = json.loads(raw)
    return result
