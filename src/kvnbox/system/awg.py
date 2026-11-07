"""AmneziaWG 3.1 (amneziawg-go): ключи, параметры маскировки, управление через UAPI-сокет.

Без внешних зависимостей: X25519 (RFC 7748) — на чистом Python (ключи создаются редко),
настройка интерфейса — текстовый протокол UAPI WireGuard по unix-сокету
`/var/run/amneziawg/<интерфейс>.sock` (amneziawg-go принимает все параметры 3.1).
"""

from __future__ import annotations

import base64
import os
import secrets
import socket
from dataclasses import dataclass

from kvnbox.core.models import AwgSettings

SOCKET_DIR = "/var/run/amneziawg"
INTERFACE = "awg0"
MTU = 1280  # рекомендация Amnezia для 3.1

# Значения AmneziaVPN по умолчанию для 3.1 (amnezia-client: protocolConstants.h) —
# сервер выглядит так же, как поставленный самой Amnezia.
TIMERS = {
    "RekeyAfterTime": "100-120",
    "RekeyTimeout": "3-7",
    "RejectAfterTime": "150-180",
    "KeepaliveTimeout": "5-15",
    "MaxHandshakeAttempts": "15-20",
}
CONTENT_PADDING = "10-100"

# ── X25519 (RFC 7748) ───────────────────────────────────────────────────────

_P = 2**255 - 19
_A24 = 121665


def _x25519(k: bytes, u: bytes) -> bytes:
    kk = bytearray(k)
    kk[0] &= 248
    kk[31] &= 127
    kk[31] |= 64
    scalar = int.from_bytes(kk, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        bit = (scalar >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3 = (da + cb) ** 2 % _P
        z3 = x1 * (da - cb) ** 2 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def public_key(private_b64: str) -> str:
    """Публичный ключ (base64, как у WireGuard) по приватному."""
    raw = _x25519(base64.b64decode(private_b64), (9).to_bytes(32, "little"))
    return base64.b64encode(raw).decode()


def private_key() -> str:
    """Новый приватный ключ X25519 (base64, с «зажатием» битов, как `wg genkey`)."""
    k = bytearray(os.urandom(32))
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return base64.b64encode(bytes(k)).decode()


def symmetric_key() -> str:
    """32 случайных байта в base64: PresharedKey, HeaderProtectionKey."""
    return base64.b64encode(os.urandom(32)).decode()


def b64_to_hex(key: str) -> str:
    raw = base64.b64decode(key)
    if len(raw) != 32:
        raise ValueError("ключ должен быть 32 байта")
    return raw.hex()


# ── параметры маскировки 3.1 ────────────────────────────────────────────────


def generate(settings: AwgSettings, port: int) -> None:
    """Ключ сервера и маскировка для первого включения (меняет `settings` на месте).
    S1–S4 ≥ 12 и S4 ≤ 32 — требования защиты заголовков; S1 + 56 ≠ S2."""
    r = secrets.SystemRandom()
    s1 = r.randint(15, 150)
    s2 = r.randint(15, 150)
    while s2 == s1 + 56:
        s2 = r.randint(15, 150)
    jmin = r.randint(40, 80)
    settings.port = port
    settings.private_key = private_key()
    settings.jc = r.randint(3, 7)
    settings.jmin = jmin
    settings.jmax = jmin + r.randint(20, 60)
    settings.s1 = s1
    settings.s2 = s2
    settings.s3 = r.randint(15, 64)
    settings.s4 = r.randint(12, 32)
    settings.header_protection_key = symmetric_key()


def shared_params(s: AwgSettings) -> dict[str, str]:
    """Одинаковые у сервера и клиента. H1–H4 = 1–4: при защите заголовков так советует
    Amnezia (заголовки шифрует HeaderProtectionKey)."""
    return {
        "S1": str(s.s1),
        "S2": str(s.s2),
        "S3": str(s.s3),
        "S4": str(s.s4),
        "H1": "1",
        "H2": "2",
        "H3": "3",
        "H4": "4",
        "HeaderProtectionKey": s.header_protection_key,
        "RandomTrailers": "on",
        "DisableCookies": "on",
    }


def server_params(s: AwgSettings) -> dict[str, str]:
    return {**shared_params(s), **TIMERS}


def client_params(s: AwgSettings) -> dict[str, str]:
    """Для [Interface] клиента: мусор перед рукопожатием и добавка к данным — только у
    клиента; таймеры — у каждой стороны свои (те же значения Amnezia)."""
    return {
        "Jc": str(s.jc),
        "Jmin": str(s.jmin),
        "Jmax": str(s.jmax),
        **shared_params(s),
        "ContentPaddingAddition": CONTENT_PADDING,
        **TIMERS,
    }


# ── UAPI ────────────────────────────────────────────────────────────────────

_UAPI_NAMES = {  # имя в .conf → имя в UAPI amneziawg-go
    "Jc": "jc",
    "Jmin": "jmin",
    "Jmax": "jmax",
    "S1": "s1",
    "S2": "s2",
    "S3": "s3",
    "S4": "s4",
    "H1": "h1",
    "H2": "h2",
    "H3": "h3",
    "H4": "h4",
    "ContentPaddingAddition": "content_padding_addition",
    "RekeyAfterTime": "rekey_after_time",
    "RekeyTimeout": "rekey_timeout",
    "RejectAfterTime": "reject_after_time",
    "KeepaliveTimeout": "keepalive_timeout",
    "MaxHandshakeAttempts": "max_handshake_attempts",
}


@dataclass(frozen=True)
class Peer:
    public_key: str
    preshared_key: str
    allowed_ip: str  # «10.8.1.N/32»


def uapi_set(s: AwgSettings, peers: list[Peer]) -> str:
    """Команда `set` UAPI для сервера: интерфейс целиком, список пиров заменяется
    (`replace_peers`) — добавление/удаление клиента без разрыва остальных."""
    lines = ["set=1", f"private_key={b64_to_hex(s.private_key)}", f"listen_port={s.port}"]
    for name, value in server_params(s).items():
        if name == "HeaderProtectionKey":
            lines.append(f"header_protection_key={b64_to_hex(value)}")
        elif name in ("RandomTrailers", "DisableCookies"):
            key = "random_trailers" if name == "RandomTrailers" else "disable_cookies"
            lines.append(f"{key}={'true' if value == 'on' else 'false'}")
        else:
            lines.append(f"{_UAPI_NAMES[name]}={value}")
    lines.append("replace_peers=true")
    for p in peers:
        lines += [
            f"public_key={b64_to_hex(p.public_key)}",
            f"preshared_key={b64_to_hex(p.preshared_key)}",
            "replace_allowed_ips=true",
            f"allowed_ip={p.allowed_ip}",
        ]
    return "\n".join(lines) + "\n\n"


class UapiError(Exception):
    pass


def uapi(request: str, interface: str = INTERFACE, timeout: float = 5.0) -> str:
    """Запрос в сокет amneziawg-go; ответ — текст; errno≠0 или нет сокета — UapiError."""
    path = f"{SOCKET_DIR}/{interface}.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(request.encode())
            data = b""
            while not data.endswith(b"\n\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise UapiError(f"amneziawg-go не отвечает ({path}): {e}") from e
    text = data.decode()
    for line in text.splitlines():
        if line.startswith("errno=") and line != "errno=0":
            raise UapiError(f"amneziawg-go отклонил настройки: {line}")
    return text


def peer_stats(text: str) -> dict[str, tuple[int, int, int]]:
    """Из ответа `get=1`: публичный ключ (hex) → (время последнего рукопожатия, rx, tx)."""
    out: dict[str, tuple[int, int, int]] = {}
    key, hs, rx, tx = "", 0, 0, 0
    for line in text.splitlines():
        name, _, value = line.partition("=")
        if name == "public_key":
            if key:
                out[key] = (hs, rx, tx)
            key, hs, rx, tx = value, 0, 0, 0
        elif name == "last_handshake_time_sec":
            hs = int(value)
        elif name == "rx_bytes":
            rx = int(value)
        elif name == "tx_bytes":
            tx = int(value)
    if key:
        out[key] = (hs, rx, tx)
    return out
