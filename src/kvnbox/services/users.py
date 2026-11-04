"""Пользователи и их подключения."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from kvnbox.clients import links
from kvnbox.core.models import Connection, CoreKind, Protocol, Transport, User
from kvnbox.core.repo import RepoError
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult

T = TypeVar("T")

# Подписи протоколов — как в меню TransferBox
SLOT_LABELS: dict[tuple[Protocol, Transport | None], str] = {
    (Protocol.VLESS, Transport.WS): "VLESS over WebSocket",
    (Protocol.VLESS, Transport.GRPC): "VLESS over gRPC",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "VLESS over XHTTP",
    (Protocol.NAIVE, None): "NaiveProxy",
    (Protocol.MIERU, None): "Mieru",
}


def slot_label(conn: Connection) -> str:
    return SLOT_LABELS[conn.slot]


class UserService:
    def __init__(self, app: App, applier: Applier | None = None) -> None:
        self.app = app
        self.applier = applier or Applier(app)

    # ── чтение ───────────────────────────────────────────────────────────────

    def all(self) -> list[User]:
        return self.app.users.all()

    def get(self, name: str) -> User:
        user = self.app.users.get(name)
        if user is None:
            raise ServiceError(f"Пользователь '{name}' не найден.")
        return user

    def connection(self, name: str, protocol: Protocol, transport: Transport | None) -> Connection:
        conn = self.get(name).find(protocol, transport)
        if conn is None:
            raise ServiceError("Подключение не найдено.")
        return conn

    def link(self, user: User, conn: Connection) -> str:
        return links.build_link(user, conn, self.app.settings.load())

    def subscription_url(self, user: User) -> str:
        return f"https://{self.app.settings.load().domain}/sub/{user.sub_token}"

    def mieru_configs(self, user: User, conn: Connection) -> dict[str, str]:
        settings = self.app.settings.load()
        return {
            "json": links.mieru_client_json(user, conn, settings),
            "clash": links.mieru_clash_yaml(user, conn, settings),
            "singbox": links.mieru_singbox_json(user, conn, settings),
        }

    def qr_terminal(self, text: str) -> str:
        res = self.app.runner.run(["qrencode", "-t", "ANSIUTF8", "-o", "-", text], timeout=15)
        if not res.ok:
            raise ServiceError("Не удалось построить QR-код (нужен пакет qrencode).")
        return res.stdout

    def qr_png(self, text: str, path: Path) -> Path:
        res = self.app.runner.run(["qrencode", "-s", "8", "-o", str(path), text], timeout=15)
        if not res.ok:
            raise ServiceError("Не удалось построить QR-код (нужен пакет qrencode).")
        return path

    # ── изменения (каждое — с применением конфигурации) ──────────────────────

    def create(
        self,
        name: str,
        protocol: Protocol,
        transport: Transport | None = None,
        core: CoreKind | None = None,
    ) -> tuple[Connection, ApplyResult]:
        if self.app.users.get(name) is not None:
            raise ServiceError(f"Пользователь с никнеймом '{name}' уже существует.")
        return self.add_connection(name, protocol, transport, core)

    def add_connection(
        self,
        name: str,
        protocol: Protocol,
        transport: Transport | None = None,
        core: CoreKind | None = None,
    ) -> tuple[Connection, ApplyResult]:
        return self._change(lambda: self.app.users.add_connection(name, protocol, transport, core))

    def remove_connection(
        self, name: str, protocol: Protocol, transport: Transport | None
    ) -> ApplyResult:
        return self._change(lambda: self.app.users.remove_connection(name, protocol, transport))[1]

    def delete(self, name: str) -> ApplyResult:
        return self._change(lambda: self.app.users.delete_user(name))[1]

    def set_enabled(
        self,
        name: str,
        enabled: bool,
        protocol: Protocol | None = None,
        transport: Transport | None = None,
    ) -> ApplyResult:
        return self._change(lambda: self.app.users.set_enabled(name, enabled, protocol, transport))[
            1
        ]

    def _change(self, mutate: Callable[[], T]) -> tuple[T, ApplyResult]:
        """Изменить данные и применить. Если применить не удалось — данные
        возвращаются к прежним, чтобы файл пользователей и рабочие конфиги совпадали."""
        before = self.app.users.load()
        try:
            value = mutate()
        except RepoError as e:
            raise ServiceError(str(e)) from e
        result = self.applier.apply()
        if not result.ok:
            self.app.users.store.save(before)
        return value, result
