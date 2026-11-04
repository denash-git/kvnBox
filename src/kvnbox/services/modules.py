"""Модули: sing-box, Mieru (mita), NetBird, BBR."""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kvnbox.core.models import Settings
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult
from kvnbox.services.settings import SettingsService
from kvnbox.system.releases import ReleaseError, Releases, version_of
from kvnbox.system.runner import Result

SINGBOX_REPO = "SagerNet/sing-box"
MIERU_REPO = "enfein/mieru"
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

    def _install_deb(self, url: str) -> None:
        with tempfile.TemporaryDirectory(dir=self.app.paths.root) as tmp:
            try:
                deb = self.releases.download(url, Path(tmp) / Path(url).name)
            except ReleaseError as e:
                raise ServiceError(str(e)) from e
            res = self.app.runner.run(["dpkg", "-i", str(deb)], timeout=300, env=APT_ENV)
            if not res.ok:
                raise ServiceError(f"Ошибка установки пакета: {res.output}")
        self.app.systemd.daemon_reload()

    def _latest(self, repo: str) -> str:
        try:
            return self.releases.latest_tag(repo)
        except ReleaseError as e:
            raise ServiceError(str(e)) from e


# ─── sing-box ────────────────────────────────────────────────────────────────


class SingboxModule(_Module):
    unit = "sing-box"

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["sing-box", "version"], timeout=10)
        version = res.stdout.split()[2] if res.ok and len(res.stdout.split()) > 2 else ""
        return ServiceInfo(
            self.unit, self.app.installed("sing-box"), self.app.systemd.state(self.unit), version
        )

    def latest_version(self) -> str:
        return version_of(self._latest(SINGBOX_REPO))

    def update(self) -> ApplyResult:
        """Поставить последний релиз и перепроверить/перезапустить конфигурацию."""
        tag = self._latest(SINGBOX_REPO)
        ver = version_of(tag)
        url = (
            f"https://github.com/{SINGBOX_REPO}/releases/download/{tag}/"
            f"sing-box_{ver}_linux_{self._arch()}.deb"
        )
        self._install_deb(url)
        result = self.applier.apply()
        return ApplyResult(result.ok, f"sing-box {ver}: {result.message}", result.log)


# ─── Mieru ───────────────────────────────────────────────────────────────────


class MieruModule(_Module):
    unit = "mita"

    def __init__(
        self, app: App, applier: Applier | None = None, releases: Releases | None = None
    ) -> None:
        super().__init__(app, applier, releases)
        self.settings = SettingsService(app, self.applier)

    def info(self) -> ServiceInfo:
        res = self.app.runner.run(["mita", "version"], timeout=10)
        return ServiceInfo(
            self.unit,
            self.app.installed("mita"),
            self.app.systemd.state(self.unit),
            res.stdout.strip() if res.ok else "",
        )

    def _deb_url(self) -> str:
        tag = self._latest(MIERU_REPO)
        return (
            f"https://github.com/{MIERU_REPO}/releases/download/{tag}/"
            f"mita_{version_of(tag)}_{self._arch()}.deb"
        )

    def install(self) -> ApplyResult:
        """Поставить mita. Как в TransferBox, модуль после установки не включается —
        это отдельное действие («Запустить службу»)."""
        self._install_deb(self._deb_url())
        return self.applier.apply()

    def update(self) -> ApplyResult:
        if not self.app.installed("mita"):
            raise ServiceError("Mieru не установлен.")
        self._install_deb(self._deb_url())
        return self.applier.apply()

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
