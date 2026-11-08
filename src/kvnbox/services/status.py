"""Сводка для шапки меню и экрана статуса бота."""

from __future__ import annotations

from dataclasses import dataclass

from kvnbox import __version__
from kvnbox.services.app import App
from kvnbox.system.files import read_text

CORE_UNITS = ("caddy", "sing-box")


@dataclass(frozen=True)
class Overview:
    version: str
    latest_version: str  # из кэша проверки обновлений; "" — неизвестно
    domain: str
    fqdn: str
    services: dict[str, str]  # unit → active/inactive/failed/not_installed
    bbr: bool
    security: bool
    mieru_enabled: bool

    @property
    def update_available(self) -> bool:
        from kvnbox.services.update import version_key

        return bool(self.latest_version) and version_key(self.latest_version) > version_key(
            self.version
        )


class StatusService:
    def __init__(self, app: App) -> None:
        self.app = app

    def update_cache(self) -> str:
        return (read_text(self.app.paths.root / ".update_cache") or "").strip()

    def overview(self) -> Overview:
        settings = self.app.settings.load()
        sd = self.app.systemd
        services = {unit: sd.state(unit) for unit in CORE_UNITS}
        services["mita"] = sd.state("mita") if self.app.installed("mita") else "not_installed"
        services["netbird"] = (
            sd.state("netbird") if self.app.installed("netbird") else "not_installed"
        )
        fqdn = self.app.runner.run(["hostname", "-f"], timeout=10).stdout.strip()
        return Overview(
            version=__version__,
            latest_version=self.update_cache(),
            domain=settings.domain,
            fqdn=fqdn or settings.fqdn,
            services=services,
            bbr=self.app.bbr.is_active(),
            security=self.app.fail2ban.is_active(),
            mieru_enabled=settings.mieru.enabled,
        )
