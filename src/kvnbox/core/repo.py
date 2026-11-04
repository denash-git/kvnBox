"""Репозитории: операции над данными поверх `JsonStore`.

Каждая изменяющая операция — одна транзакция (блокировка + атомарная запись),
поэтому TUI и бот могут работать одновременно.
Применение изменений к ядрам (рендер + перезапуск) — забота сервисного слоя.
"""

from __future__ import annotations

from contextlib import AbstractContextManager

from kvnbox.core.models import (
    USER_NAME_RE,
    Connection,
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    User,
    UsersDB,
    UuidCredentials,
    default_core,
    new_password,
    new_username,
    supported_cores,
)
from kvnbox.core.paths import Paths
from kvnbox.core.storage import JsonStore


class RepoError(Exception):
    """Ошибка операции, понятная пользователю (сообщение показывается в TUI/боте)."""


class SettingsRepo:
    def __init__(self, paths: Paths) -> None:
        self.store: JsonStore[Settings] = JsonStore(paths.settings_file, Settings)

    def load(self) -> Settings:
        return self.store.load()

    def save(self, settings: Settings) -> None:
        self.store.save(settings)

    def transaction(self) -> AbstractContextManager[Settings]:
        return self.store.transaction()


class UsersRepo:
    def __init__(self, paths: Paths) -> None:
        self.store: JsonStore[UsersDB] = JsonStore(paths.users_file, UsersDB, default=UsersDB)

    def load(self) -> UsersDB:
        return self.store.load()

    def all(self) -> list[User]:
        return self.load().users

    def get(self, name: str) -> User | None:
        return self.load().get(name)

    # ── изменения ────────────────────────────────────────────────────────────

    def add_connection(
        self,
        name: str,
        protocol: Protocol,
        transport: Transport | None = None,
        core: CoreKind | None = None,
    ) -> Connection:
        """Добавить подключение; пользователь создаётся, если его нет."""
        if core is None:
            try:
                core = default_core(protocol, transport)
            except ValueError as e:
                raise RepoError(str(e)) from e
        elif core not in supported_cores(protocol, transport):
            label = _slot_label(protocol, transport)
            raise RepoError(f"Ядро {core.value} не поддерживает {label}.")

        with self.store.transaction() as db:
            user = db.get(name)
            if user is None:
                if not USER_NAME_RE.fullmatch(name):
                    raise RepoError("Имя: только латиница, цифры, «_» и «-», до 32 символов.")
                user = User(name=name)
                db.users.append(user)
            if user.find(protocol, transport):
                raise RepoError(
                    f"У пользователя '{name}' уже есть {_slot_label(protocol, transport)}."
                )
            conn = Connection(
                protocol=protocol,
                transport=transport,
                core=core,
                credentials=_new_credentials(protocol, db),
            )
            user.connections.append(conn)
            return conn

    def remove_connection(self, name: str, protocol: Protocol, transport: Transport | None) -> None:
        """Удалить подключение; вместе с последним подключением удаляется и пользователь
        (как в TransferBox, где пользователь — это набор записей)."""
        with self.store.transaction() as db:
            user = self._require(db, name)
            conn = user.find(protocol, transport)
            if conn is None:
                raise RepoError("Подключение не найдено.")
            user.connections.remove(conn)
            _prune_empty(db)

    def delete_user(self, name: str) -> None:
        with self.store.transaction() as db:
            user = self._require(db, name)
            db.users.remove(user)

    def set_enabled(
        self,
        name: str,
        enabled: bool,
        protocol: Protocol | None = None,
        transport: Transport | None = None,
    ) -> None:
        """Включить/выключить все подключения пользователя или одно конкретное."""
        with self.store.transaction() as db:
            user = self._require(db, name)
            if protocol is None:
                targets = user.connections
            else:
                conn = user.find(protocol, transport)
                if conn is None:
                    raise RepoError("Подключение не найдено.")
                targets = [conn]
            for c in targets:
                c.enabled = enabled

    @staticmethod
    def _require(db: UsersDB, name: str) -> User:
        user = db.get(name)
        if user is None:
            raise RepoError("Пользователь не найден.")
        return user


def _new_credentials(protocol: Protocol, db: UsersDB) -> UuidCredentials | PasswordCredentials:
    if protocol is Protocol.VLESS:
        taken = {
            c.credentials.uuid
            for u in db.users
            for c in u.connections
            if isinstance(c.credentials, UuidCredentials)
        }
        while True:
            creds = UuidCredentials()
            if creds.uuid not in taken:
                return creds
    taken_names = db.usernames()
    username = new_username()
    while username in taken_names:
        username = new_username()
    return PasswordCredentials(username=username, password=new_password())


def _prune_empty(db: UsersDB) -> None:
    # В TransferBox пользователь = набор записей; без записей его нет.
    db.users[:] = [u for u in db.users if u.connections]


def _slot_label(protocol: Protocol, transport: Transport | None) -> str:
    labels = {
        (Protocol.VLESS, Transport.WS): "VLESS over WS",
        (Protocol.VLESS, Transport.GRPC): "VLESS over GRPC",
        (Protocol.VLESS, Transport.HTTPUPGRADE): "VLESS over HTTPUpgrade",
        (Protocol.NAIVE, None): "NaiveProxy",
        (Protocol.MIERU, None): "Mieru",
    }
    return labels.get((protocol, transport), protocol.value)
