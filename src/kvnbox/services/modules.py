"""Модули: sing-box, Mieru (mita), NetBird, BBR."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from kvnbox.core.models import CoreKind, Settings, XraySettings
from kvnbox.cores.xray import xray_running
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult
from kvnbox.services.settings import SettingsService
from kvnbox.system.releases import Asset, ReleaseError, Releases, version_of
from kvnbox.system.runner import Result
from kvnbox.system.xray_unit import ensure_xray_unit

SINGBOX_REPO = "SagerNet/sing-box"
MIERU_REPO = "enfein/mieru"
CADDY_REPO = "caddyserver/caddy"
XRAY_REPO = "XTLS/Xray-core"
NETBIRD_INSTALL_SCRIPT = "https://pkgs.netbird.io/install.sh"
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


@dataclass(frozen=True)
class ServiceInfo:
    unit: str
    installed: bool
    state: str  # active / inactive / failed … / not_installed
    version: str = ""


class _Module:
    unit = ""

    def __init__(
        self, app: App, applier: Applier | None = None, releases: Releases | None = None
    ) -> None:
        self.app = app
        self.applier = applier or Applier(app)
        self.releases = releases or Releases()

    def start(self) -> Result:
        return self.app.systemd.start(self.unit)

    def stop(self) -> Result:
        return self.app.systemd.stop(self.unit)

    def restart(self) -> Result:
        return self.app.systemd.restart_clean(self.unit)

    def logs(self, lines: int = 100) -> str:
        return self.app.systemd.journal(self.unit, lines)

    def _arch(self) -> str:
        res = self.app.runner.run(["dpkg", "--print-architecture"], timeout=10)
        arch = res.stdout.strip() if res.ok else ""
        if arch not in ("amd64", "arm64"):
            raise ServiceError(f"Неподдерживаемая архитектура: {arch or 'неизвестно'}")
        return arch

    def _latest(self, repo: str) -> str:
        try:
            return self.releases.latest_tag(repo)
        except ReleaseError as e:
            raise ServiceError(str(e)) from e


@dataclass(frozen=True)
class UpdateOutcome:
    ok: bool
    message: str
    rolled_back: bool = False


class _DebCore(_Module):
    """Ядро из deb-пакета GitHub-релиза: обновление только из меню (D-041) — с проверкой
    контрольной суммы и возвратом прежней версии, если новая не заработала."""

    repo = ""
    binary = ""  # файл программы — запасная копия, если старый пакет не скачать
    name = ""

    def asset_name(self, version: str, arch: str) -> str:
        raise NotImplementedError

    def info(self) -> ServiceInfo:
        raise NotImplementedError

    def should_run(self) -> bool:
        return True

    def latest_version(self) -> str:
        return version_of(self._latest(self.repo))

    def _download(self, tag: str, arch: str, dest: Path) -> Path:
        ver = version_of(tag)
        name = self.asset_name(ver, arch)
        try:
            return self.releases.download_verified(
                self.releases.asset(self.repo, tag, name), dest / name
            )
        except ReleaseError as e:
            raise ServiceError(str(e)) from e

    def _dpkg(self, deb: Path) -> None:
        res = self.app.runner.run(["dpkg", "-i", str(deb)], timeout=300, env=APT_ENV)
        if not res.ok:
            raise ServiceError(f"Ошибка установки пакета: {res.output}")
        self.app.systemd.daemon_reload()

    def _wait_active(self, seconds: int = 10) -> bool:
        for _ in range(seconds):
            if self.app.systemd.is_active(self.unit):
                return True
            time.sleep(1)
        return self.app.systemd.is_active(self.unit)

    def install_verified(self, tag: str | None = None) -> None:
        """Поставить релиз (по умолчанию последний) с проверкой контрольной суммы."""
        tag = tag or self._latest(self.repo)
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            self._dpkg(self._download(tag, self._arch(), Path(tmp)))

    def safe_update(self, tag: str | None = None) -> UpdateOutcome:
        current = self.info().version
        tag = tag or self._latest(self.repo)
        ver = version_of(tag)
        arch = self._arch()
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            work = Path(tmp)
            new = self._download(tag, arch, work / "new")  # ошибка здесь — ничего не тронуто
            old: Path | None = None
            if current:
                try:
                    old = self._download(f"v{current}", arch, work / "old")
                except ServiceError:
                    old = None  # старого релиза нет — вернём копию программы
            backup = work / "binary.prev"
            binary = Path(self.binary)
            if binary.exists():
                shutil.copy2(binary, backup)
            try:
                self._dpkg(new)
                result = self.applier.apply()
                ok = result.ok and (not self.should_run() or self._wait_active())
                reason = result.message if not result.ok else "служба не запустилась"
            except ServiceError as e:
                ok, reason = False, str(e)
            if ok:
                return UpdateOutcome(True, f"{self.name} обновлён до версии {ver}.")
            # возврат прежней версии
            if old is not None:
                self._dpkg(old)
            elif backup.exists():
                shutil.copy2(backup, binary)
            self.applier.apply()
        return UpdateOutcome(
            False,
            f"Версия {ver} не заработала ({reason}). Возвращена версия {current or 'прежняя'}.",
            rolled_back=True,
        )


# ─── sing-box ────────────────────────────────────────────────────────────────


class SingboxModule(_DebCore):
    unit = "sing-box"
    repo = SINGBOX_REPO
    binary = "/usr/bin/sing-box"
    name = "sing-box"

    def asset_name(self, version: str, arch: str) -> str:
        return f"sing-box_{version}_linux_{arch}.deb"

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["sing-box", "version"], timeout=10)
        version = res.stdout.split()[2] if res.ok and len(res.stdout.split()) > 2 else ""
        return ServiceInfo(
            self.unit, self.app.installed("sing-box"), self.app.systemd.state(self.unit), version
        )


# ─── Xray (этап 11) ──────────────────────────────────────────────────────────


XRAY_ARCH = {"amd64": "64", "arm64": "arm64-v8a"}


def read_versions(path: Path) -> dict[str, str]:
    """versions.env панели: KEY=VALUE, комментарии — «#»."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    return dict(
        line.split("=", 1)
        for line in (raw.strip() for raw in text.splitlines())
        if line and not line.startswith("#") and "=" in line
    )


class XrayModule(_Module):
    """Xray: программа из архива GitHub (закреплённая версия — versions.env), служба
    `xray` от своего пользователя. Служба работает, только когда есть подключения на
    Xray и он не отключён в меню (Q-011)."""

    unit = "xray"

    def __init__(
        self, app: App, applier: Applier | None = None, releases: Releases | None = None
    ) -> None:
        super().__init__(app, applier, releases)
        self.settings = SettingsService(app, self.applier)

    # ── состояние ────────────────────────────────────────────────────────────

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["xray", "version"], timeout=10)
        words = res.stdout.split()
        version = words[1] if res.ok and len(words) > 1 else ""
        return ServiceInfo(
            self.unit, self.app.installed("xray"), self.app.systemd.state(self.unit), version
        )

    def connections_count(self) -> int:
        return sum(
            1 for u in self.app.users.all() for c in u.connections if c.core is CoreKind.XRAY
        )

    def latest_version(self) -> str:
        return version_of(self._latest(XRAY_REPO))

    # ── установка ────────────────────────────────────────────────────────────

    def _archive_name(self) -> str:
        return f"Xray-linux-{XRAY_ARCH[self._arch()]}.zip"

    def _put_binary(self, archive: Path) -> None:
        """Достать из архива только программу `xray` и атомарно заменить."""
        target = self.app.paths.xray_bin
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name("xray.new")
        with zipfile.ZipFile(archive) as z, z.open("xray") as src, tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        tmp.chmod(0o755)
        os.replace(tmp, target)

    def _prepare_system(self) -> None:
        if not self.app.runner.run(["id", "-u", "xray"], timeout=10).ok:
            res = self.app.runner.run(
                ["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", "xray"],
                timeout=30,
            )
            if not res.ok:
                raise ServiceError(f"Не удалось создать пользователя xray: {res.output}")
        conf_dir = self.app.paths.xray_config.parent
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_dir.chmod(0o750)
        self.app.runner.run(["chown", "root:xray", str(conf_dir)], timeout=10)
        ensure_xray_unit(self.app.paths, self.app.systemd)

    def install(self) -> ApplyResult:
        """Поставить закреплённую версию (versions.env панели) и задать случайные пути."""
        versions = read_versions(self.app.paths.app_link / "versions.env")
        arch = self._arch()
        version = versions.get("XRAY_VERSION", "")
        sha = versions.get(f"XRAY_SHA256_{arch.upper()}", "")
        if not version or not sha:
            raise ServiceError("В versions.env панели нет версии Xray.")
        name = self._archive_name()
        asset = Asset(
            name=name,
            url=f"https://github.com/{XRAY_REPO}/releases/download/v{version}/{name}",
            sha256=sha,
        )
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            try:
                archive = self.releases.download_verified(asset, Path(tmp) / name)
            except ReleaseError as e:
                raise ServiceError(str(e)) from e
            self._put_binary(archive)
        self._prepare_system()

        def paths(s: Settings) -> None:
            if not s.xray.configured:
                s.xray = XraySettings.generated()

        return self.settings.update(paths)

    def safe_update(self, tag: str | None = None) -> UpdateOutcome:
        """Обновление из меню: сумма из GitHub, замена программы, возврат при сбое."""
        if not self.app.installed("xray"):
            raise ServiceError("Xray не установлен.")
        current = self.info().version
        tag = tag or self._latest(XRAY_REPO)
        ver = version_of(tag)
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            work = Path(tmp)
            try:
                asset = self.releases.asset(XRAY_REPO, tag, self._archive_name())
                archive = self.releases.download_verified(asset, work / asset.name)
            except ReleaseError as e:
                raise ServiceError(str(e)) from e  # ничего не тронуто
            backup = work / "xray.prev"
            shutil.copy2(self.app.paths.xray_bin, backup)
            self._put_binary(archive)
            result = self.applier.apply()
            running = xray_running(self.app.settings.load(), self.app.users.all())
            ok = result.ok and (not running or self._wait_active())
            if ok:
                return UpdateOutcome(True, f"Xray обновлён до версии {ver}.")
            reason = result.message if not result.ok else "служба не запустилась"
            shutil.copy2(backup, self.app.paths.xray_bin)
            self.applier.apply()
        return UpdateOutcome(
            False,
            f"Версия {ver} не заработала ({reason}). Возвращена версия {current or 'прежняя'}.",
            rolled_back=True,
        )

    def _wait_active(self, seconds: int = 10) -> bool:
        for _ in range(seconds):
            if self.app.systemd.is_active(self.unit):
                return True
            time.sleep(1)
        return self.app.systemd.is_active(self.unit)

    # ── включение / удаление ─────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> ApplyResult:
        def change(s: Settings) -> None:
            s.xray.enabled = enabled

        return self.settings.update(change)

    def uninstall(self) -> ApplyResult:
        if self.connections_count():
            raise ServiceError("На Xray есть подключения — сначала удалите их.")
        self.app.systemd.stop(self.unit)
        self.app.systemd.disable(self.unit)
        self.app.paths.xray_unit.unlink(missing_ok=True)
        self.app.systemd.daemon_reload()
        self.app.paths.xray_bin.unlink(missing_ok=True)
        shutil.rmtree(self.app.paths.xray_config.parent, ignore_errors=True)

        def reset(s: Settings) -> None:
            s.xray = XraySettings()  # пути создадутся заново при следующей установке

        return self.settings.update(reset)


# ─── Caddy ───────────────────────────────────────────────────────────────────


class CaddyModule(_Module):
    """Caddy собирается с NaiveProxy (xcaddy), поэтому обновление — пересборка
    установщиком в режиме --rebuild-caddy (до 5 минут, с возвратом прежней версии)."""

    unit = "caddy"

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["caddy", "version"], timeout=10)
        version = res.stdout.split()[0] if res.ok and res.stdout.split() else ""
        return ServiceInfo(
            self.unit, self.app.installed("caddy"), self.app.systemd.state(self.unit), version
        )

    def latest_version(self) -> str:
        tag = self._latest(CADDY_REPO)
        if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
            raise ServiceError(f"Неожиданная версия Caddy: {tag}")
        return tag

    def rebuild_command(self, tag: str) -> list[str]:
        return ["bash", str(self.app.paths.app_link / "install.sh"), "--rebuild-caddy", tag]


# ─── Mieru ───────────────────────────────────────────────────────────────────


class MieruModule(_DebCore):
    unit = "mita"
    repo = MIERU_REPO
    binary = "/usr/bin/mita"
    name = "Mieru (mita)"

    def __init__(
        self, app: App, applier: Applier | None = None, releases: Releases | None = None
    ) -> None:
        super().__init__(app, applier, releases)
        self.settings = SettingsService(app, self.applier)

    def asset_name(self, version: str, arch: str) -> str:
        return f"mita_{version}_{arch}.deb"

    def should_run(self) -> bool:
        return self.app.settings.load().mieru.enabled

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["mita", "version"], timeout=10)
        return ServiceInfo(
            self.unit,
            self.app.installed("mita"),
            self.app.systemd.state(self.unit),
            res.stdout.strip() if res.ok else "",
        )

    def install(self) -> ApplyResult:
        """Поставить mita. Модуль после установки не включается — это отдельное
        действие («Включить Mieru»)."""
        self.install_verified()
        return self.applier.apply()

    def safe_update(self, tag: str | None = None) -> UpdateOutcome:
        if not self.app.installed("mita"):
            raise ServiceError("Mieru не установлен.")
        return super().safe_update(tag)

    def set_enabled(self, enabled: bool) -> ApplyResult:
        def change(s: Settings) -> None:
            s.mieru.enabled = enabled

        return self.settings.update(change)

    def set_port(self, port: int) -> ApplyResult:
        if not 1 <= port <= 65535:
            raise ServiceError("Порт: 1-65535.")

        def change(s: Settings) -> None:
            s.mieru.port = port

        return self.settings.update(change)

    def uninstall(self) -> ApplyResult:
        self.app.systemd.stop(self.unit)
        self.app.systemd.disable(self.unit)
        self.app.runner.run(["dpkg", "-P", "mita"], timeout=120, env=APT_ENV)
        mita_dir = self.app.paths.mita_config.parent
        for f in (
            self.app.paths.mita_config,
            mita_dir / "server.conf.pb",
            self.app.paths.mita_service_override,
        ):
            f.unlink(missing_ok=True)
        self.app.systemd.daemon_reload()
        return self.set_enabled(False)  # уберёт правила фаервола Mieru


# ─── NetBird ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NetbirdStatus:
    installed: bool
    state: str
    ip: str = ""
    peers: str = ""


class NetbirdModule(_Module):
    unit = "netbird"

    def __init__(
        self, app: App, applier: Applier | None = None, releases: Releases | None = None
    ) -> None:
        super().__init__(app, applier, releases)
        self.settings = SettingsService(app, self.applier)

    def status(self) -> NetbirdStatus:
        if not self.app.installed("netbird"):
            return NetbirdStatus(False, "not_installed")
        state = self.app.systemd.state(self.unit)
        ip = peers = ""
        if state == "active":
            out = self.app.runner.run(["netbird", "status"], timeout=20).stdout
            m = re.search(r"NetBird IP:\s*(\S+)", out, re.I)
            ip = m.group(1) if m else ""
            m = re.search(r"Peers count:\s*(.+)", out, re.I)
            peers = m.group(1).strip() if m else ""
        return NetbirdStatus(True, state, ip, peers)

    def connection_state(self) -> str:
        """not_installed / stopped / needs_login / connected / disconnected."""
        if not self.app.installed("netbird"):
            return "not_installed"
        if not self.app.systemd.is_active(self.unit):
            return "stopped"
        res = self.app.runner.run(["netbird", "status"], timeout=20)
        out = (res.stdout + res.stderr).lower()  # как `netbird status 2>&1`
        if "needslogin" in out:
            return "needs_login"
        if "connected" in out and "management: disconnected" not in out:
            return "connected"
        return "disconnected"

    def detailed_status(self) -> str:
        return self.app.runner.run(["netbird", "status", "-d"], timeout=30).output

    def install(self) -> None:
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            try:
                script = self.releases.download(NETBIRD_INSTALL_SCRIPT, Path(tmp) / "install.sh")
            except ReleaseError as e:
                raise ServiceError(str(e)) from e
            res = self.app.runner.run(["sh", str(script)], timeout=600, env=APT_ENV)
        if not res.ok:
            raise ServiceError(f"Установка NetBird не удалась: {res.output}")
        self.app.systemd.enable(self.unit, now=True)

    def set_setup_key(self, key: str) -> None:
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9-]+", key):
            raise ServiceError("Setup Key: латиница, цифры и «-».")
        self.settings.update(lambda s: setattr(s.netbird, "setup_key", key), apply=False)

    def set_management_url(self, url: str) -> None:
        url = url.strip().rstrip("/")
        if url and not re.match(r"https?://", url):
            url = f"https://{url}"  # как в TransferBox
        if url and not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[\w./-]*)?", url):
            raise ServiceError("Management URL: адрес вида https://netbird.example.com")
        self.settings.update(lambda s: setattr(s.netbird, "management_url", url), apply=False)

    def up(self) -> Result:
        nb = self.app.settings.load().netbird
        if not nb.setup_key:
            raise ServiceError("Сначала задайте Setup Key.")
        argv = ["netbird", "up", "--setup-key", nb.setup_key]
        if nb.management_url:
            argv += ["--management-url", nb.management_url]
        return self.app.runner.run(argv, timeout=120)

    def update(self) -> Result:
        self.app.runner.run(["apt-get", "update", "-qq"], timeout=300, env=APT_ENV)
        return self.app.runner.run(
            ["apt-get", "install", "--only-upgrade", "-y", "netbird"], timeout=600, env=APT_ENV
        )

    def uninstall(self) -> None:
        self.app.systemd.stop(self.unit)
        self.app.systemd.disable(self.unit)
        self.app.runner.run(
            ["apt-get", "remove", "--purge", "-y", "netbird"], timeout=600, env=APT_ENV
        )
        etc = self.app.paths.etc
        (etc / "apt" / "sources.list.d" / "netbird.list").unlink(missing_ok=True)
        keyring = self.app.paths.etc.parent / "usr" / "share" / "keyrings"
        (keyring / "netbird-archive-keyring.gpg").unlink(missing_ok=True)
        self.app.runner.run(["apt-get", "update", "-qq"], timeout=300, env=APT_ENV)

        def clear(s: Settings) -> None:
            s.netbird.setup_key = ""
            s.netbird.management_url = ""

        self.settings.update(clear, apply=False)


# ─── BBR ─────────────────────────────────────────────────────────────────────


class BbrModule:
    def __init__(self, app: App) -> None:
        self.app = app
        self.settings = SettingsService(app)

    def is_active(self) -> bool:
        return self.app.bbr.is_active()

    def toggle(self) -> str:
        """Переключить BBR. Возвращает сообщение для пользователя."""
        if self.app.bbr.is_active():
            if not self.app.bbr.disable():
                raise ServiceError("Ошибка при отключении BBR.")
            self.settings.update(lambda s: setattr(s, "bbr_enabled", False), apply=False)
            msg = "BBR оптимизация успешно отключена (переключено на cubic)."
            foreign = self.app.bbr.foreign_sources()
            if foreign:
                names = ", ".join(str(p) for p in foreign)
                msg += f"\nВнимание: BBR включится снова после перезагрузки из {names}."
            return msg
        if not self.app.bbr.enable():
            raise ServiceError("Ядро вашей системы не поддерживает BBR.")
        self.settings.update(lambda s: setattr(s, "bbr_enabled", True), apply=False)
        return "BBR оптимизация успешно включена!"
