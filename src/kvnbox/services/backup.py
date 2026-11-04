"""Резервные копии настроек и пользователей; восстановление с досинхронизацией системы."""

from __future__ import annotations

import datetime as dt
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from kvnbox.core.models import Settings, UsersDB
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier
from kvnbox.services.schedule import write_cron
from kvnbox.services.settings import SettingsService

PREFIX = "kvnbox_backup_"
SETTINGS_NAME = "settings.json"
USERS_NAME = "users.json"


class PasswordRequired(ServiceError):
    """Архив зашифрован, а пароль из настроек не подошёл (или не задан)."""


@dataclass
class RestoreResult:
    ok: bool
    message: str
    log: list[str] = field(default_factory=list)


def is_encrypted(path: Path) -> bool:
    with zipfile.ZipFile(path) as zf:
        return any(info.flag_bits & 0x1 for info in zf.infolist())


class BackupService:
    def __init__(self, app: App, applier: Applier | None = None) -> None:
        self.app = app
        self.applier = applier or Applier(app)
        self.settings = SettingsService(app, self.applier)

    @property
    def directory(self) -> Path:
        return self.app.paths.backups_dir

    # ── создание ─────────────────────────────────────────────────────────────

    def create(self, password: str | None = None, label: str = "") -> Path:
        """Архив settings.json + users.json. С паролем — `zip -P` (как в TransferBox,
        открывается любым архиватором), без пароля — обычный zip."""
        sources = [self.app.paths.settings_file, self.app.paths.users_file]
        missing = [p.name for p in sources if not p.exists()]
        if missing:
            raise ServiceError(f"Нет файлов для бэкапа: {', '.join(missing)}")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"{PREFIX}{label + '_' if label else ''}{stamp}{'_pass' if password else ''}.zip"
        dest = self.directory / name
        if password:
            self._ensure_zip()
            res = self.app.runner.run(
                ["zip", "-q", "-j", "-P", password, str(dest), *map(str, sources)], timeout=60
            )
            if not res.ok:
                raise ServiceError(f"Ошибка создания zip: {res.output}")
        else:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in sources:
                    zf.write(p, arcname=p.name)
        dest.chmod(0o600)
        return dest

    def _ensure_zip(self) -> None:
        if not self.app.installed("zip"):
            env = {"DEBIAN_FRONTEND": "noninteractive"}
            self.app.runner.run(["apt-get", "update", "-qq"], timeout=300, env=env)
            self.app.runner.run(["apt-get", "install", "-y", "-qq", "zip"], timeout=300, env=env)

    def all(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(
            self.directory.glob(f"{PREFIX}*.zip"), key=lambda p: p.stat().st_mtime, reverse=True
        )

    def latest(self) -> Path | None:
        backups = self.all()
        return backups[0] if backups else None

    def set_password(self, password: str) -> None:
        self.settings.update(lambda s: setattr(s.backup, "password", password), apply=False)

    def set_schedule(
        self, enabled: bool, hour: int | None = None, minute: int | None = None
    ) -> None:
        def change(s: Settings) -> None:
            s.backup.enabled = enabled
            if hour is not None:
                s.backup.hour = hour
            if minute is not None:
                s.backup.minute = minute

        self.settings.update(change, apply=False)
        write_cron(self.app.settings.load(), self.app.paths)

    # ── восстановление ───────────────────────────────────────────────────────

    def read(self, path: Path, password: str | None = None) -> tuple[Settings, UsersDB]:
        """Прочитать и проверить содержимое архива, ничего не меняя."""
        try:
            encrypted = is_encrypted(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise ServiceError("Файл повреждён или это не zip-архив.") from e
        if encrypted and password is None:
            saved = (
                self.app.settings.load().backup.password if self.app.settings.store.exists() else ""
            )
            if not saved:
                raise PasswordRequired("Архив защищен паролем. Введите пароль для расшифровки.")
            password = saved
        pwd = password.encode("utf-8") if password else None
        try:
            with zipfile.ZipFile(path) as zf:
                names = {Path(n).name: n for n in zf.namelist()}
                if SETTINGS_NAME not in names or USERS_NAME not in names:
                    raise ServiceError(
                        "В архиве нет settings.json и users.json — это не бэкап kvnBox."
                    )
                settings_raw = zf.read(names[SETTINGS_NAME], pwd=pwd)
                users_raw = zf.read(names[USERS_NAME], pwd=pwd)
        except RuntimeError as e:  # неверный пароль
            raise PasswordRequired("Неверный пароль архива.") from e
        except zipfile.BadZipFile as e:
            raise ServiceError("Файл повреждён или это не zip-архив.") from e
        try:
            return Settings.model_validate_json(settings_raw), UsersDB.model_validate_json(
                users_raw
            )
        except ValidationError as e:
            raise ServiceError(f"Данные в архиве повреждены:\n{e}") from e

    def restore(self, path: Path, password: str | None = None) -> RestoreResult:
        settings, users = self.read(path, password)
        log: list[str] = []
        if self.app.paths.settings_file.exists() and self.app.paths.users_file.exists():
            safety = self.create(label="pre_restore")
            log.append(f"текущее состояние сохранено: {safety.name}")
        self.app.settings.save(settings)
        self.app.users.store.save(users)
        log.append(f"восстановлено: пользователей {len(users.users)}")
        log += self._sync_system(settings)
        result = self.applier.apply()
        log += result.log
        return RestoreResult(result.ok, result.message, log)

    def _sync_system(self, settings: Settings) -> list[str]:
        """Привести систему к восстановленным настройкам (как TransferBox при restore)."""
        from kvnbox.services.modules import BbrModule, MieruModule, NetbirdModule

        log: list[str] = []
        if settings.fqdn:
            current = self.app.runner.run(["hostname"], timeout=10).stdout.strip()
            if current and current != settings.fqdn:
                try:
                    self.settings.set_fqdn(settings.fqdn)
                    log.append(f"имя хоста: {settings.fqdn}")
                except ServiceError as e:
                    log.append(f"имя хоста не изменено: {e}")
        bbr = BbrModule(self.app)
        if settings.bbr_enabled != bbr.is_active():
            try:
                log.append(bbr.toggle())
            except ServiceError as e:
                log.append(str(e))
        if settings.mieru.enabled and not self.app.installed("mita"):
            try:
                MieruModule(self.app, self.applier).install()
                log.append("Mieru установлен")
            except ServiceError as e:
                log.append(f"Mieru не установлен: {e}")
        if settings.netbird.setup_key and not self.app.installed("netbird"):
            nb = NetbirdModule(self.app, self.applier)
            try:
                nb.install()
                nb.up()
                log.append("NetBird установлен и подключён")
            except ServiceError as e:
                log.append(f"NetBird: {e}")
        write_cron(settings, self.app.paths)
        log.append("расписание задач обновлено")
        return log
