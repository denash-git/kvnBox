"""Управление SSH: порт, вход по паролю/ключу, ключи."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.settings import SettingsService
from kvnbox.system.sshd import AuthMode, AuthorizedKey, SshError

SSH_RULE_COMMENT = "SSH Port"


@dataclass(frozen=True)
class SshStatus:
    port: int
    auth_mode: AuthMode
    keys: list[AuthorizedKey]

    @property
    def password_allowed(self) -> bool:
        return self.auth_mode == "password"


class SshService:
    def __init__(self, app: App) -> None:
        self.app = app
        self.settings = SettingsService(app)

    def status(self) -> SshStatus:
        sshd = self.app.sshd
        return SshStatus(sshd.port(), sshd.auth_mode(), sshd.authorized_keys())

    # ── порт ─────────────────────────────────────────────────────────────────

    def change_port(self, new_port: int) -> int:
        """Сменить порт SSH. Возвращает прежний порт.

        Порядок как в TransferBox: сначала открыть новый порт в фаерволе (чтобы не
        потерять доступ), затем sshd (с откатом, если не поднялся), затем fail2ban.
        Старое правило фаервола убирается отдельно — `confirm_port_change`, после
        того как пользователь подтвердил вход по новому порту.
        """
        old = self.app.sshd.port()
        if new_port == old:
            raise ServiceError(f"SSH уже настроен на порт {new_port}.")
        if not 1 <= new_port <= 65535:
            raise ServiceError("Неверный порт. Диапазон: 1-65535.")
        self.app.ufw.allow(f"{new_port}/tcp", SSH_RULE_COMMENT)
        try:
            self.app.sshd.set_port(new_port)
        except SshError as e:
            self.app.ufw.delete_allow(f"{new_port}/tcp")
            raise ServiceError(str(e)) from e
        self.app.fail2ban.update_ssh_port(new_port)
        return old

    def confirm_port_change(self, old_port: int) -> None:
        """Убрать правило старого порта. Порт 22 оставляется (как в TransferBox)."""
        if old_port and old_port != 22:
            self.app.ufw.delete_allow(f"{old_port}/tcp")

    # ── вход ─────────────────────────────────────────────────────────────────

    def set_auth_mode(self, mode: AuthMode) -> str:
        if self.app.sshd.auth_mode() == mode:
            return (
                "Вход по паролю уже отключен." if mode == "key" else "Вход по паролю уже включен."
            )
        try:
            self.app.sshd.set_auth_mode(mode)
        except SshError as e:
            raise ServiceError(str(e)) from e
        self.settings.update(lambda s: setattr(s.ssh, "auth_mode", mode), apply=False)
        return (
            "Вход по паролю успешно отключен."
            if mode == "key"
            else "Вход по паролю успешно включен."
        )

    def generate_key(self) -> Path:
        try:
            return self.app.sshd.generate_key()
        except SshError as e:
            raise ServiceError(str(e)) from e

    def add_key(self, public_key: str) -> None:
        try:
            self.app.sshd.add_key(public_key)
        except SshError as e:
            raise ServiceError(str(e)) from e

    def delete_key(self, index: int) -> str:
        try:
            key, password_enabled = self.app.sshd.delete_key(index)
        except SshError as e:
            raise ServiceError(str(e)) from e
        if password_enabled:
            self.settings.update(lambda s: setattr(s.ssh, "auth_mode", "password"), apply=False)
            return (
                f"Ключ #{index} удален. Ключей больше нет — вход по паролю автоматически включен."
            )
        return f"Ключ #{index} ({key.comment}) успешно удален."
