"""Управление Telegram-ботом: токен, служба systemd, оповещения, проверка связи."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kvnbox.core.models import Settings
from kvnbox.core.storage import atomic_write_text
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.notify import NotifyError, Telegram
from kvnbox.services.schedule import write_cron
from kvnbox.services.settings import SettingsService

UNIT = "kvnbox-bot"


def unit_file(app: App) -> str:
    cli = (app.paths.root / ".venv" / "bin" / "kvnbox").as_posix()
    return (
        "[Unit]\n"
        "Description=kvnBox Telegram Bot\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "User=root\n"
        f"WorkingDirectory={app.paths.root.as_posix()}\n"
        f"ExecStart={cli} bot run\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "Restart=on-failure\n"
        "RestartSec=10\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


@dataclass(frozen=True)
class BotState:
    configured: bool
    chat_id: str
    enabled: bool
    running: bool
    alerts: bool


class BotService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.settings = SettingsService(app)

    @property
    def unit_path(self) -> object:
        return self.app.paths.etc / "systemd" / "system" / f"{UNIT}.service"

    def state(self) -> BotState:
        tg = self.app.settings.load().telegram
        return BotState(
            configured=bool(tg.token and tg.chat_id),
            chat_id=tg.chat_id,
            enabled=tg.enabled,
            running=self.app.systemd.is_active(UNIT),
            alerts=tg.alerts_enabled,
        )

    def configure(self, token: str, chat_id: str) -> None:
        token, chat_id = token.strip(), chat_id.strip()
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
            raise ServiceError("Токен выглядит неверно (формат: 123456789:AAH...).")
        if not re.fullmatch(r"-?\d+", chat_id):
            raise ServiceError("Chat ID — это число (например, 123456789).")

        def change(s: Settings) -> None:
            s.telegram.token = token
            s.telegram.chat_id = chat_id

        self.settings.update(change, apply=False)
        if self.app.settings.load().telegram.enabled:
            self.app.systemd.restart(UNIT)

    def ensure_unit(self) -> None:
        """Как в TransferBox: если бот включён, а службы нет — создать и запустить."""
        tg = self.app.settings.load().telegram
        path = self.app.paths.etc / "systemd" / "system" / f"{UNIT}.service"
        if tg.enabled and tg.token and tg.chat_id and not path.exists():
            self._install_unit()

    def _install_unit(self) -> None:
        path = self.app.paths.etc / "systemd" / "system" / f"{UNIT}.service"
        atomic_write_text(path, unit_file(self.app), 0o644)
        self.app.systemd.daemon_reload()
        self.app.systemd.enable(UNIT)
        self.app.systemd.restart(UNIT)
        write_cron(self.app.settings.load(), self.app.paths)

    def enable(self) -> bool:
        """Включить бота и оповещения. True — тестовое сообщение доставлено."""
        tg = self.app.settings.load().telegram
        if not (tg.token and tg.chat_id):
            raise ServiceError("Ошибка: Сначала настройте Токен и Chat ID!")

        def change(s: Settings) -> None:
            s.telegram.enabled = True
            s.telegram.alerts_enabled = True

        self.settings.update(change, apply=False)
        self._install_unit()
        return self.send_test(
            "🤖 <b>Бот kvnBox успешно запущен!</b>\nПанель управления теперь доступна в Telegram."
        )

    def disable(self) -> None:
        self.app.systemd.stop(UNIT)
        self.app.systemd.disable(UNIT)
        (self.app.paths.etc / "systemd" / "system" / f"{UNIT}.service").unlink(missing_ok=True)
        self.app.systemd.daemon_reload()
        (self.app.paths.etc / "cron.d" / "kvnbox").unlink(missing_ok=True)
        self.settings.update(lambda s: setattr(s.telegram, "enabled", False), apply=False)

    def toggle_alerts(self) -> bool:
        new = not self.app.settings.load().telegram.alerts_enabled
        self.settings.update(lambda s: setattr(s.telegram, "alerts_enabled", new), apply=False)
        return new

    def send_test(
        self, text: str = "🔔 <b>Тестовое оповещение:</b> Проверка связи выполнена успешно!"
    ) -> bool:
        """Отправить сообщение напрямую (в TransferBox «успех» показывался всегда)."""
        try:
            Telegram(self.app.settings.load().telegram).send_message(text)
        except NotifyError:
            return False
        return True
