"""Модели данных kvnBox (pydantic v2).

Пользователь владеет набором подключений. Каждое подключение — это протокол
(+ транспорт для VLESS), ядро, которое его обслуживает, и учётные данные.
Поле `core` заложено под будущий выбор ядра (Xray) при создании подключения.
"""

from __future__ import annotations

import re
import secrets
import uuid
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# ─── Перечисления ────────────────────────────────────────────────────────────


class Protocol(str, Enum):
    VLESS = "vless"
    NAIVE = "naive"
    MIERU = "mieru"


class Transport(str, Enum):
    WS = "ws"
    GRPC = "grpc"
    # В меню и ссылках TransferBox называется «XHTTP», но в sing-box это HTTPUpgrade (D-015)
    HTTPUPGRADE = "httpupgrade"


class CoreKind(str, Enum):
    SINGBOX = "singbox"
    CADDY = "caddy"
    MITA = "mita"


# Какие ядра умеют обслуживать протокол/транспорт. Первое — ядро по умолчанию.
SUPPORTED_CORES: dict[tuple[Protocol, Transport | None], tuple[CoreKind, ...]] = {
    (Protocol.VLESS, Transport.WS): (CoreKind.SINGBOX,),
    (Protocol.VLESS, Transport.GRPC): (CoreKind.SINGBOX,),
    (Protocol.VLESS, Transport.HTTPUPGRADE): (CoreKind.SINGBOX,),
    (Protocol.NAIVE, None): (CoreKind.CADDY,),
    (Protocol.MIERU, None): (CoreKind.MITA,),
}


def supported_cores(protocol: Protocol, transport: Transport | None) -> tuple[CoreKind, ...]:
    return SUPPORTED_CORES.get((protocol, transport), ())


def default_core(protocol: Protocol, transport: Transport | None) -> CoreKind:
    cores = supported_cores(protocol, transport)
    if not cores:
        raise ValueError(f"Нет ядра для {protocol.value}/{transport.value if transport else '-'}")
    return cores[0]


# ─── Учётные данные ──────────────────────────────────────────────────────────


class UuidCredentials(_Model):
    kind: Literal["uuid"] = "uuid"
    uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("uuid")
    @classmethod
    def _valid_uuid(cls, v: str) -> str:
        return str(uuid.UUID(v))


class PasswordCredentials(_Model):
    kind: Literal["password"] = "password"
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


Credentials = Annotated[
    Union[UuidCredentials, PasswordCredentials],  # noqa: UP007 — дискриминатор pydantic
    Field(discriminator="kind"),
]

_CREDENTIALS_KIND = {
    Protocol.VLESS: "uuid",
    Protocol.NAIVE: "password",
    Protocol.MIERU: "password",
}


def new_password() -> str:
    return secrets.token_hex(12)


def new_username() -> str:
    return "user_" + secrets.token_hex(4)


def new_sub_token() -> str:
    return secrets.token_hex(8)


def new_id() -> str:
    return secrets.token_hex(4)


# ─── Пользователи ────────────────────────────────────────────────────────────


class Connection(_Model):
    id: str = Field(default_factory=new_id)
    protocol: Protocol
    transport: Transport | None = None
    core: CoreKind
    enabled: bool = True
    credentials: Credentials

    @model_validator(mode="after")
    def _consistent(self) -> Connection:
        if self.protocol is Protocol.VLESS and self.transport is None:
            raise ValueError("для VLESS нужен транспорт")
        if self.protocol is not Protocol.VLESS and self.transport is not None:
            raise ValueError(f"у {self.protocol.value} не бывает транспорта")
        if self.core not in supported_cores(self.protocol, self.transport):
            raise ValueError(
                f"ядро {self.core.value} не поддерживает {self.protocol.value}"
                + (f"/{self.transport.value}" if self.transport else "")
            )
        if self.credentials.kind != _CREDENTIALS_KIND[self.protocol]:
            raise ValueError(f"неверный тип учётных данных для {self.protocol.value}")
        return self

    @property
    def slot(self) -> tuple[Protocol, Transport | None]:
        """У пользователя не может быть двух подключений с одинаковым slot."""
        return (self.protocol, self.transport)


# Имя: как в Telegram-боте TransferBox. Используется при создании пользователя;
# при загрузке старых данных допускается любое имя без пробелов и «:».
USER_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")


class User(_Model):
    name: str = Field(min_length=1, max_length=64)
    sub_token: str = Field(default_factory=new_sub_token, min_length=8)
    connections: list[Connection] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_chars(cls, v: str) -> str:
        if any(ch.isspace() for ch in v) or ":" in v:
            raise ValueError("имя не должно содержать пробелов и «:»")
        return v

    @model_validator(mode="after")
    def _unique_slots(self) -> User:
        slots = [c.slot for c in self.connections]
        if len(slots) != len(set(slots)):
            raise ValueError(f"у пользователя {self.name} повторяются подключения")
        ids = [c.id for c in self.connections]
        if len(ids) != len(set(ids)):
            raise ValueError(f"у пользователя {self.name} повторяются id подключений")
        return self

    @property
    def active(self) -> bool:
        return any(c.enabled for c in self.connections)

    def find(self, protocol: Protocol, transport: Transport | None = None) -> Connection | None:
        return next((c for c in self.connections if c.slot == (protocol, transport)), None)


class UsersDB(_Model):
    schema_version: int = SCHEMA_VERSION
    users: list[User] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique(self) -> UsersDB:
        _ensure_unique("имя пользователя", [u.name for u in self.users])
        _ensure_unique("токен подписки", [u.sub_token for u in self.users])
        conns = [c for u in self.users for c in u.connections]
        _ensure_unique(
            "UUID",
            [c.credentials.uuid for c in conns if isinstance(c.credentials, UuidCredentials)],
        )
        for proto in (Protocol.NAIVE, Protocol.MIERU):
            _ensure_unique(
                f"логин {proto.value}",
                [
                    c.credentials.username
                    for c in conns
                    if c.protocol is proto and isinstance(c.credentials, PasswordCredentials)
                ],
            )
        return self

    def get(self, name: str) -> User | None:
        return next((u for u in self.users if u.name == name), None)

    def usernames(self) -> set[str]:
        return {
            c.credentials.username
            for u in self.users
            for c in u.connections
            if isinstance(c.credentials, PasswordCredentials)
        }


def _ensure_unique(what: str, values: list[str]) -> None:
    seen: set[str] = set()
    for v in values:
        if v in seen:
            raise ValueError(f"повторяется {what}: {v}")
        seen.add(v)


# ─── Настройки инстанса ──────────────────────────────────────────────────────

FAKE_SITE_TEMPLATES = ("aether", "chronos", "nexus", "stratum", "synapse")


def _url_path(v: str) -> str:
    if not v.startswith("/"):
        raise ValueError("путь должен начинаться с /")
    if any(ch.isspace() for ch in v):
        raise ValueError("путь не должен содержать пробелов")
    return v


class SiteSettings(_Model):
    template: str = "aether"

    @field_validator("template")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in FAKE_SITE_TEMPLATES:
            raise ValueError(f"неизвестный шаблон сайта: {v}")
        return v


class VlessSettings(_Model):
    ws_path: str = "/"
    grpc_service: str = ""
    httpupgrade_path: str = "/xhttp"

    @field_validator("ws_path", "httpupgrade_path")
    @classmethod
    def _paths(cls, v: str) -> str:
        return _url_path(v)

    @field_validator("grpc_service")
    @classmethod
    def _service(cls, v: str) -> str:
        if any(ch.isspace() for ch in v):
            raise ValueError("имя gRPC-сервиса не должно содержать пробелов")
        return v.lstrip("/")


class MieruSettings(_Model):
    enabled: bool = False
    port: int = Field(default=21000, ge=1, le=65535)


class BackupSettings(_Model):
    enabled: bool = False
    hour: int = Field(default=12, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    password: str = ""


class TelegramSettings(_Model):
    enabled: bool = False
    token: str = ""
    chat_id: str = ""
    alerts_enabled: bool = False


class SecuritySettings(_Model):
    scanner_protection: bool = False


class SshSettings(_Model):
    auth_mode: Literal["password", "key"] = "password"


class NetbirdSettings(_Model):
    setup_key: str = ""
    management_url: str = ""


class Settings(_Model):
    schema_version: int = SCHEMA_VERSION
    domain: str = Field(min_length=1)
    admin_email: str = ""
    fqdn: str = ""
    bbr_enabled: bool = True
    site: SiteSettings = Field(default_factory=SiteSettings)
    vless: VlessSettings = Field(default_factory=VlessSettings)
    mieru: MieruSettings = Field(default_factory=MieruSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ssh: SshSettings = Field(default_factory=SshSettings)
    netbird: NetbirdSettings = Field(default_factory=NetbirdSettings)

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", v):
            raise ValueError(f"некорректный домен: {v}")
        return v
