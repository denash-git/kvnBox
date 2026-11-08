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
    AWG = "awg"  # AmneziaWG 3.1 (этап 13)


class Transport(str, Enum):
    WS = "ws"
    GRPC = "grpc"
    # В TransferBox назывался «XHTTP», но это HTTPUpgrade
    HTTPUPGRADE = "httpupgrade"
    # Настоящий XHTTP — только в Xray (этап 11)
    XHTTP = "xhttp"
    # XHTTP через CDN: данные в заголовках GET, packet-up (только Xray, только через CDN)
    CDN = "cdn"


class CoreKind(str, Enum):
    SINGBOX = "singbox"
    XRAY = "xray"
    CADDY = "caddy"
    MITA = "mita"
    AWG = "awg"  # amneziawg-go


# Какие ядра умеют обслуживать протокол/транспорт. Первое — ядро по умолчанию.
# Ядро выбирается при добавлении подключения и дальше не меняется.
SUPPORTED_CORES: dict[tuple[Protocol, Transport | None], tuple[CoreKind, ...]] = {
    (Protocol.VLESS, Transport.WS): (CoreKind.SINGBOX, CoreKind.XRAY),
    (Protocol.VLESS, Transport.GRPC): (CoreKind.SINGBOX, CoreKind.XRAY),
    (Protocol.VLESS, Transport.HTTPUPGRADE): (CoreKind.SINGBOX, CoreKind.XRAY),
    (Protocol.VLESS, Transport.XHTTP): (CoreKind.XRAY,),
    (Protocol.VLESS, Transport.CDN): (CoreKind.XRAY,),
    (Protocol.NAIVE, None): (CoreKind.CADDY,),
    (Protocol.MIERU, None): (CoreKind.MITA,),
    (Protocol.AWG, None): (CoreKind.AWG,),
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


# Логин/пароль попадают в Caddyfile (`basic_auth user pass`) и в URL ссылок —
# допускаем только «безопасные» символы.
_TOKEN_RE = re.compile(r"[A-Za-z0-9._~!$&*+=@^-]+")


class PasswordCredentials(_Model):
    kind: Literal["password"] = "password"
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username", "password")
    @classmethod
    def _safe(cls, v: str) -> str:
        if not _TOKEN_RE.fullmatch(v):
            raise ValueError("недопустимые символы (пробелы, кавычки, скобки, «:», «/», «#»)")
        return v


_B64_KEY_RE = re.compile(r"[A-Za-z0-9+/]{43}=")

# Сеть клиентов AmneziaWG: сервер — .1, клиенты — .2…254 (как у Amnezia по умолчанию)
AWG_SUBNET = "10.8.1.0/24"
AWG_SERVER_ADDRESS = "10.8.1.1"


def _awg_key(v: str) -> str:
    if not _B64_KEY_RE.fullmatch(v):
        raise ValueError("ключ AmneziaWG: 32 байта в base64")
    return v


class AwgCredentials(_Model):
    """Клиент AmneziaWG: свой ключ, общий с сервером PresharedKey, адрес в сети клиентов."""

    kind: Literal["awg"] = "awg"
    private_key: str
    preshared_key: str
    address: str

    @field_validator("private_key", "preshared_key")
    @classmethod
    def _key(cls, v: str) -> str:
        return _awg_key(v)

    @field_validator("address")
    @classmethod
    def _address(cls, v: str) -> str:
        if not re.fullmatch(r"10\.8\.1\.([2-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-4])", v):
            raise ValueError(f"адрес клиента AmneziaWG вне сети {AWG_SUBNET}: {v}")
        return v


Credentials = Annotated[
    Union[UuidCredentials, PasswordCredentials, AwgCredentials],  # noqa: UP007 — дискриминатор
    Field(discriminator="kind"),
]

_CREDENTIALS_KIND = {
    Protocol.VLESS: "uuid",
    Protocol.NAIVE: "password",
    Protocol.MIERU: "password",
    Protocol.AWG: "awg",
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


# Имя при создании пользователя (проверяется через fullmatch): латиница в любом
# регистре, цифры (в т.ч. первым символом), «_» и «-». В реальных данных есть «A1».
# При загрузке допускается любое имя без пробелов и «:».
USER_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")


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
        awg = [c.credentials for c in conns if isinstance(c.credentials, AwgCredentials)]
        _ensure_unique("адрес AmneziaWG", [a.address for a in awg])
        _ensure_unique("ключ AmneziaWG", [a.private_key for a in awg])
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

FAKE_SITE_TEMPLATES = (
    "aether",
    "chronos",
    "nexus",
    "stratum",
    "synapse",
    # этап 12: копии страниц входа self-hosted хранилищ
    "nextcloud",
    "ocis",
    "sftpgo",
    "seafile",
    "immich",
)

# подписи шаблонов для меню (TUI и бот), в порядке показа
SITE_TEMPLATE_LABELS: tuple[tuple[str, str], ...] = (
    ("Aether Resonance (Эфирный Резонанс)", "aether"),
    ("Quantum Nexus (Квантовый Нексус)", "nexus"),
    ("Synaptic Dynamics (Синаптическая Динамика)", "synapse"),
    ("Chronos Temporal (Хроно-Вектор)", "chronos"),
    ("Stratum Synergy (Стратум Синергия)", "stratum"),
    ("Облако: Nextcloud", "nextcloud"),
    ("Облако: ownCloud Infinite Scale", "ocis"),
    ("Хранилище: SFTPGo", "sftpgo"),
    ("Облако: Seafile", "seafile"),
    ("Фотоархив: Immich", "immich"),
)


_URL_PATH_RE = re.compile(r"/[A-Za-z0-9/._~-]*")


def _url_path(v: str) -> str:
    # путь вставляется в Caddyfile и sing-box: только безопасные символы
    if not _URL_PATH_RE.fullmatch(v):
        raise ValueError("путь: начинается с /, допустимы латиница, цифры и / . _ ~ -")
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
    # новые установки; у переехавших с TransferBox в settings.json остаётся «/xhttp»
    httpupgrade_path: str = "/httpupgrade"

    @field_validator("ws_path", "httpupgrade_path")
    @classmethod
    def _paths(cls, v: str) -> str:
        return _url_path(v)

    @field_validator("grpc_service")
    @classmethod
    def _service(cls, v: str) -> str:
        return _grpc_service(v)


def _grpc_service(v: str) -> str:
    v = v.lstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9._~-]*", v):
        raise ValueError("имя gRPC-сервиса: латиница, цифры и . _ ~ -")
    return v


def random_path() -> str:
    """Случайный путь для новой установки: `/a8f3k2q1`."""
    return "/" + random_name(8)


def random_name(length: int = 8) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class XraySettings(_Model):
    """Ядро Xray (этап 11). Пути пустые, пока Xray не установлен — создаются один раз
    при установке (случайные) и дальше меняются только вручную в меню."""

    enabled: bool = True  # «Отключить Xray» в меню: служба и маршруты убираются
    ws_path: str = ""
    grpc_service: str = ""
    httpupgrade_path: str = ""
    xhttp_path: str = ""
    cdn_path: str = ""  # создаётся при первой настройке CDN

    @field_validator("ws_path", "httpupgrade_path", "xhttp_path", "cdn_path")
    @classmethod
    def _paths(cls, v: str) -> str:
        return v if v == "" else _url_path(v)

    @field_validator("grpc_service")
    @classmethod
    def _service(cls, v: str) -> str:
        return _grpc_service(v)

    @property
    def configured(self) -> bool:
        return all((self.ws_path, self.grpc_service, self.httpupgrade_path, self.xhttp_path))

    @classmethod
    def generated(cls) -> XraySettings:
        return cls(
            ws_path=random_path(),
            grpc_service=random_name(8),
            httpupgrade_path=random_path(),
            xhttp_path=random_path(),
        )


_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")


class CdnSettings(_Model):
    """CDN (этап 11): клиенты ходят на `domain` (держит CDN), CDN приходит на сервер
    с именем `origin_host` — на него Caddy получает свой сертификат."""

    domain: str = ""
    origin_host: str = ""

    @field_validator("domain", "origin_host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip().lower()
        if v and not _HOST_RE.fullmatch(v):
            raise ValueError(f"некорректное имя: {v}")
        return v

    @property
    def configured(self) -> bool:
        return bool(self.domain and self.origin_host)


class MieruSettings(_Model):
    enabled: bool = False
    port: int = Field(default=21000, ge=1, le=65535)


class AwgSettings(_Model):
    """AmneziaWG 3.1 (этап 13). Ставится вместе с панелью, но выключен. При первом
    включении создаются ключ сервера, случайный высокий UDP-порт и маскировка (общие
    для всех клиентов: смена — перевыпуск всех конфигов)."""

    enabled: bool = False
    port: int = Field(default=0, ge=0, le=65535)  # 0 — ещё не выбран
    private_key: str = ""
    jc: int = Field(default=0, ge=0, le=128)
    jmin: int = Field(default=0, ge=0, le=1280)
    jmax: int = Field(default=0, ge=0, le=1280)
    s1: int = Field(default=0, ge=0, le=1132)
    s2: int = Field(default=0, ge=0, le=1188)
    s3: int = Field(default=0, ge=0, le=1216)
    s4: int = Field(default=0, ge=0, le=32)
    header_protection_key: str = ""

    @field_validator("private_key", "header_protection_key")
    @classmethod
    def _key(cls, v: str) -> str:
        return v if v == "" else _awg_key(v)

    @property
    def configured(self) -> bool:
        return bool(self.private_key and self.header_protection_key and self.port)


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
    xray: XraySettings = Field(default_factory=XraySettings)
    cdn: CdnSettings = Field(default_factory=CdnSettings)
    mieru: MieruSettings = Field(default_factory=MieruSettings)
    awg: AwgSettings = Field(default_factory=AwgSettings)
    backup: BackupSettings = Field(default_factory=BackupSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    ssh: SshSettings = Field(default_factory=SshSettings)
    netbird: NetbirdSettings = Field(default_factory=NetbirdSettings)
    # скрытый путь замера скорости через 443 (веб-лаунчер); пусто — маршрута нет
    speedtest_path: str = ""

    @field_validator("admin_email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", v):
            raise ValueError(f"некорректный email: {v}")
        return v

    @model_validator(mode="after")
    def _cdn_hosts(self) -> Settings:
        names = [n for n in (self.domain, self.cdn.domain, self.cdn.origin_host) if n]
        if len(names) != len(set(names)):
            raise ValueError("основной домен, домен CDN и origin-поддомен должны различаться")
        return self

    @model_validator(mode="after")
    def _distinct_paths(self) -> Settings:
        """Caddy разводит трафик по ядрам по пути (этап 11) — пути не должны совпадать."""
        paths = [
            ("sing-box WS", self.vless.ws_path),
            ("sing-box HTTPUpgrade", self.vless.httpupgrade_path),
            ("Xray WS", self.xray.ws_path),
            ("Xray HTTPUpgrade", self.xray.httpupgrade_path),
            ("Xray XHTTP", self.xray.xhttp_path),
            ("Xray CDN", self.xray.cdn_path),
        ]
        if self.vless.grpc_service:
            paths.append(("sing-box gRPC", "/" + self.vless.grpc_service))
        if self.xray.grpc_service:
            paths.append(("Xray gRPC", "/" + self.xray.grpc_service))
        used = [(name, p.rstrip("/") or "/") for name, p in paths if p]
        for i, (name, p) in enumerate(used):
            if p == "/sub" or p.startswith("/sub/"):
                raise ValueError(f"{name}: путь {p} занят подписками")
            for other, q in used[i + 1 :]:
                if p == q:
                    raise ValueError(f"{name} и {other}: одинаковый путь {p}")
        xhttp = self.xray.xhttp_path.rstrip("/")
        if xhttp:
            for name, p in used:
                if name != "Xray XHTTP" and p.startswith(xhttp + "/"):
                    raise ValueError(f"{name}: путь {p} внутри пути XHTTP {xhttp}")
        return self

    @field_validator("domain")
    @classmethod
    def _domain(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", v):
            raise ValueError(f"некорректный домен: {v}")
        return v
