"""Пользователи и их подключения."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from kvnbox.clients import awg as awg_client
from kvnbox.clients import links
from kvnbox.core.models import Connection, CoreKind, Protocol, Transport, User
from kvnbox.core.repo import RepoError
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult
from kvnbox.services.notify import Telegram

T = TypeVar("T")

# Подписи протоколов — как в меню TransferBox
SLOT_LABELS: dict[tuple[Protocol, Transport | None], str] = {
    (Protocol.VLESS, Transport.WS): "VLESS over WebSocket",
    (Protocol.VLESS, Transport.GRPC): "VLESS over gRPC",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "VLESS over HTTPUpgrade",
    (Protocol.VLESS, Transport.XHTTP): "VLESS over XHTTP",
    (Protocol.VLESS, Transport.CDN): "VLESS CDN",
    (Protocol.NAIVE, None): "NaiveProxy",
    (Protocol.MIERU, None): "Mieru",
    (Protocol.AWG, None): "AmneziaWG",
}

# Ядра, у которых есть выбор (VLESS); у Naive и Mieru ядро одно — не показываем
CORE_LABELS = {CoreKind.SINGBOX: "sing-box", CoreKind.XRAY: "Xray"}


def core_suffix(conn: Connection) -> str:
    """« · sing-box» / « · Xray» для VLESS, иначе пусто (этап 11)."""
    label = CORE_LABELS.get(conn.core)
    return f" · {label}" if label and conn.protocol is Protocol.VLESS else ""


def slot_label(conn: Connection) -> str:
    return SLOT_LABELS[conn.slot]


# Подписи в меню управления пользователем: «VLESS over ${type^^}» в TransferBox
MANAGE_LABELS: dict[tuple[Protocol, Transport | None], str] = {
    (Protocol.VLESS, Transport.WS): "VLESS over WS",
    (Protocol.VLESS, Transport.GRPC): "VLESS over GRPC",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "VLESS over HTTPUpgrade",
    (Protocol.VLESS, Transport.XHTTP): "VLESS over XHTTP",
    (Protocol.VLESS, Transport.CDN): "VLESS CDN",
    (Protocol.NAIVE, None): "NaiveProxy",
    (Protocol.MIERU, None): "Mieru",
    (Protocol.AWG, None): "AmneziaWG",
}

# Краткие подписи в списке пользователей
SHORT_LABELS: dict[tuple[Protocol, Transport | None], str] = {
    (Protocol.VLESS, Transport.WS): "VLESS WS",
    (Protocol.VLESS, Transport.GRPC): "VLESS gRPC",
    (Protocol.VLESS, Transport.HTTPUPGRADE): "VLESS HTTPUpgrade",
    (Protocol.VLESS, Transport.XHTTP): "VLESS XHTTP",
    (Protocol.VLESS, Transport.CDN): "VLESS CDN",
    (Protocol.NAIVE, None): "Naive",
    (Protocol.MIERU, None): "Mieru",
    (Protocol.AWG, None): "AmneziaWG",
}


def _telegram_label(conn: Connection) -> str:
    # «{proto.upper()} {type.upper()}» в TransferBox (у naive/mieru типа нет)
    if conn.protocol is Protocol.VLESS and conn.transport is not None:
        legacy = {"httpupgrade": "HTTPUpgrade"}.get(
            conn.transport.value, conn.transport.value.upper()
        )
        return f"VLESS {legacy}"
    return conn.protocol.value.upper()


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

    def awg_conf(self, user: User, conn: Connection) -> str:
        return awg_client.client_conf(user, conn, self.app.settings.load())

    def awg_vpn_key(self, user: User, conn: Connection) -> str:
        return awg_client.vpn_key(user, conn, self.app.settings.load())

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

    def send_to_telegram(self, name: str) -> None:
        """Ссылки, QR-коды подключений и подписки — в чат администратора
        (как send_user_links в TransferBox)."""
        import tempfile

        from kvnbox.services.notify import NotifyError

        telegram = Telegram(self.app.settings.load().telegram)
        user = self.get(name)
        sub = self.subscription_url(user)
        parts = [
            f"🔑 <b>Ссылки для {user.name}:</b>\n",
            "🌀 <b>Ссылка подписки:</b>",
            f"<code>{sub}</code>\n",
        ]
        labeled = [
            (_telegram_label(c), self.link(user, c))
            for c in user.connections
            if c.protocol is not Protocol.AWG  # у AmneziaWG — файл, QR и ключ отдельно
        ]
        for label, link in labeled:
            parts += [f"🌐 <b>{label}:</b>", f"<code>{link}</code>\n"]
        try:
            telegram.send_message("\n".join(parts))
            with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
                png = Path(tmp) / "qr.png"
                for label, link in labeled:
                    telegram.send_photo(self.qr_png(link, png), f"QR-код для {user.name} ({label})")
                telegram.send_photo(self.qr_png(sub, png), f"QR-код подписки для {user.name}")
                awg_conn = user.find(Protocol.AWG)
                if awg_conn is not None:
                    self.send_awg(telegram, user, awg_conn, Path(tmp))
        except NotifyError as e:
            raise ServiceError(str(e)) from e

    def send_awg(self, telegram: Telegram, user: User, conn: Connection, tmp: Path) -> None:
        """AmneziaWG в Telegram: файл .conf, его QR-код, ключ vpn:// и его QR-код."""
        conf_path = tmp / awg_client.file_name(user)
        conf = self.awg_conf(user, conn)
        conf_path.write_text(conf, encoding="utf-8")
        key = self.awg_vpn_key(user, conn)
        telegram.send_document(conf_path, f"AmneziaWG 3.1 для {user.name}: файл конфигурации")
        telegram.send_photo(
            self.qr_png(conf, tmp / "awg-conf.png"),
            f"QR-код AmneziaWG для {user.name} (приложения AmneziaWG и AmneziaVPN)",
        )
        telegram.send_message(f"🔐 <b>AmneziaVPN, ключ для {user.name}:</b>\n<code>{key}</code>")
        telegram.send_photo(
            self.qr_png(key, tmp / "awg-key.png"), f"QR-код ключа AmneziaVPN для {user.name}"
        )

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
        wants_xray = core is CoreKind.XRAY or transport in (Transport.XHTTP, Transport.CDN)
        if wants_xray and not self.xray_ready():
            raise ServiceError("Xray не установлен. Установите: Модули → Xray → Установить Xray.")
        if transport is Transport.CDN and not self.cdn_ready():
            raise ServiceError("CDN не настроен: Модули → Настройки CDN.")
        return self._change(lambda: self.app.users.add_connection(name, protocol, transport, core))

    def cdn_ready(self) -> bool:
        """Протокол CDN: Xray готов, заданы домен CDN, origin-поддомен и путь."""
        s = self.app.settings.load()
        return self.xray_ready() and s.cdn.configured and bool(s.xray.cdn_path)

    def xray_ready(self) -> bool:
        """Можно ли добавлять подключения на Xray: программа стоит, пути заданы."""
        return self.app.installed("xray") and self.app.settings.load().xray.configured

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
