"""AmneziaWG 3.1 на сервере (этап 13): настройка интерфейса после старта службы, NAT,
состояние клиентов. Модуль «включить/выключить/порт/сборка» — в services/modules.py.

Порядок `sync` (после старта `amneziawg-go`, из ExecStartPost и после изменений клиентов):
ключи и клиенты — через UAPI (без разрыва остальных), адрес и MTU — `ip`, пересылка —
sysctl, NAT — своя таблица nftables `kvnbox_awg` (UFW её не трогает, панель снимает при
остановке).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from kvnbox.core.models import AWG_SERVER_ADDRESS, AWG_SUBNET, AwgCredentials, CoreKind, User
from kvnbox.cores.base import active_connections
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.system import awg

NFT_TABLE = "kvnbox_awg"
UNIT = "kvnbox-awg"


def nat_ruleset() -> str:
    return (
        f"table ip {NFT_TABLE} {{\n"
        "    chain postrouting {\n"
        "        type nat hook postrouting priority srcnat; policy accept;\n"
        f'        ip saddr {AWG_SUBNET} oifname != "{awg.INTERFACE}" masquerade\n'
        "    }\n"
        "}\n"
    )


def peers(users: list[User]) -> list[awg.Peer]:
    """Включённые клиенты AmneziaWG (выключенные — не пускаем: их нет в списке пиров)."""
    out = []
    for _, c in active_connections(users, CoreKind.AWG):
        if isinstance(c.credentials, AwgCredentials):
            out.append(
                awg.Peer(
                    awg.public_key(c.credentials.private_key),
                    c.credentials.preshared_key,
                    f"{c.credentials.address}/32",
                )
            )
    return out


@dataclass(frozen=True)
class AwgPeerState:
    user: str
    address: str
    last_handshake: int  # unix-время, 0 — ещё не было
    rx: int
    tx: int


class AwgService:
    def __init__(self, app: App) -> None:
        self.app = app

    def _run(self, *cmd: str, input: str | None = None) -> None:
        res = self.app.runner.run(list(cmd), timeout=30, input=input)
        if not res.ok:
            raise ServiceError(f"{' '.join(cmd[:3])}: {res.output.strip()}")

    def sync(self, wait: float = 10.0) -> str:
        """Настроить работающий `amneziawg-go`. Сообщение или ServiceError."""
        settings = self.app.settings.load()
        s = settings.awg
        if not s.configured:
            raise ServiceError("AmneziaWG ещё не включался: нет ключей и порта.")
        sock = Path(awg.SOCKET_DIR) / f"{awg.INTERFACE}.sock"
        deadline = time.monotonic() + wait
        while not sock.exists():
            if time.monotonic() > deadline:
                raise ServiceError("amneziawg-go не запустился (нет управляющего сокета).")
            time.sleep(0.2)
        clients = peers(self.app.users.all())
        try:
            awg.uapi(awg.uapi_set(s, clients))
        except awg.UapiError as e:
            raise ServiceError(str(e)) from e
        self._run("ip", "address", "replace", f"{AWG_SERVER_ADDRESS}/24", "dev", awg.INTERFACE)
        self._run("ip", "link", "set", "dev", awg.INTERFACE, "mtu", str(awg.MTU), "up")
        self._run("sysctl", "-q", "-w", "net.ipv4.ip_forward=1")
        self.app.runner.run(["nft", "delete", "table", "ip", NFT_TABLE], timeout=15)
        self._run("nft", "-f", "-", input=nat_ruleset())
        return f"AmneziaWG: порт {s.port}/udp, клиентов {len(clients)}"

    def down(self) -> str:
        """После остановки службы: убрать NAT (интерфейс исчезает вместе с программой)."""
        self.app.runner.run(["nft", "delete", "table", "ip", NFT_TABLE], timeout=15)
        return "NAT AmneziaWG снят"

    def peer_states(self) -> list[AwgPeerState]:
        """Клиенты и их последнее рукопожатие (для мониторинга). Пусто — служба не отвечает."""
        try:
            stats = awg.peer_stats(awg.uapi("get=1\n\n"))
        except awg.UapiError:
            return []
        out = []
        for user, c in active_connections(self.app.users.all(), CoreKind.AWG):
            if not isinstance(c.credentials, AwgCredentials):
                continue
            key = awg.b64_to_hex(awg.public_key(c.credentials.private_key))
            hs, rx, tx = stats.get(key, (0, 0, 0))
            out.append(AwgPeerState(user.name, c.credentials.address, hs, rx, tx))
        return out
