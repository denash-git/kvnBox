"""Применение конфигурации: данные → конфиги ядер → проверка → перезапуск.

Отличие от TransferBox: новые конфиги проверяются (`caddy validate`,
`sing-box check`) **до** замены рабочих файлов, а если служба после замены
не поднялась — рабочие файлы возвращаются к прежним и службы перезапускаются.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from kvnbox.core.models import Settings
from kvnbox.core.storage import atomic_write_text
from kvnbox.cores.base import RenderedFile
from kvnbox.cores.xray import xray_running
from kvnbox.render import RenderPlan, render_all
from kvnbox.services.app import App
from kvnbox.services.schedule import write_cron
from kvnbox.system.caddy_unit import ensure_caddy_unit
from kvnbox.system.files import read_text, replace_directory, write_file
from kvnbox.system.xray_unit import ensure_xray_unit

CADDY = "caddy"
SINGBOX = "sing-box"
MITA = "mita"
XRAY = "xray"
MIERU_RULE_COMMENTS = ("Mieru TCP", "Mieru UDP")


@dataclass
class ApplyResult:
    ok: bool
    message: str
    log: list[str] = field(default_factory=list)


class Applier:
    def __init__(self, app: App) -> None:
        self.app = app
        self.paths = app.paths

    # ── публичное ────────────────────────────────────────────────────────────

    def apply(self) -> ApplyResult:
        settings = self.app.settings.load()
        users = self.app.users.all()
        plan = render_all(settings, users, self.paths)
        log: list[str] = []
        # Xray (этап 11): служба работает, только когда есть его подключения
        xray_installed = self.app.installed("xray")
        want_xray = xray_installed and xray_running(settings, users)
        had_xray = xray_installed and self.app.systemd.is_active(XRAY)

        error = self.validate(plan)
        if error:
            return ApplyResult(False, error, log)
        log.append("конфигурации проверены")

        self.sync_fakesite()
        # служба caddy — тоже часть конфигурации: исправления приходят с обновлением
        unit_changed = ensure_caddy_unit(self.paths, self.app.systemd)
        if unit_changed:
            log.append("служба caddy обновлена")
        previous = {f.path: read_text(f.path) for f in plan.files}
        for f in plan.files:
            write_file(f)
        log.append(f"записано файлов: {len(plan.files)}")

        problem = self._restart_core(full=unit_changed, xray=want_xray if xray_installed else None)
        if problem:
            self._rollback(plan, previous, xray=had_xray if xray_installed else None)
            return ApplyResult(False, f"{problem}. Прежняя конфигурация восстановлена.", log)
        log.append("caddy и sing-box перезапущены")
        if want_xray:
            log.append("xray перезапущен")
        elif had_xray:
            log.append("xray остановлен: подключений на Xray нет или он отключён")

        # подписки — только когда новая конфигурация уже работает
        replace_directory(self.paths.sub_dir, plan.subscriptions)
        log.append(f"подписок: {len(plan.subscriptions)}")

        mita_note = self._apply_mita(
            settings, mita_changed=_changed(plan, previous, self._mita_paths)
        )
        if mita_note:
            log.append(mita_note)

        self.app.ufw.delete_port("80/tcp")  # порт 80 закрыт для скрытности
        # расписание тоже часть конфигурации: новые задачи приходят с обновлением панели
        write_cron(settings, self.paths)
        return ApplyResult(True, "Конфигурация применена, службы перезапущены.", log)

    def validate(self, plan: RenderPlan) -> str | None:
        """Проверить новые конфиги во временных файлах. Текст ошибки или None."""
        staging = self.paths.root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        try:
            for f in plan.files:
                if f.path == self.paths.caddy_config:
                    tmp = staging / "Caddyfile"
                    atomic_write_text(tmp, f.content)
                    res = self.app.runner.run(
                        ["caddy", "validate", "--adapter", "caddyfile", "--config", str(tmp)],
                        timeout=60,
                    )
                    if not res.ok:
                        return f"Ошибка в конфигурации Caddy:\n{res.output}"
                elif f.path == self.paths.xray_config and self.app.installed("xray"):
                    tmp = staging / "xray.json"
                    atomic_write_text(tmp, f.content)
                    res = self.app.runner.run(
                        ["xray", "run", "-test", "-config", str(tmp)], timeout=60
                    )
                    if not res.ok:
                        return f"Ошибка в конфигурации Xray:\n{res.output}"
                elif f.path == self.paths.singbox_config:
                    tmp = staging / "sing-box.json"
                    atomic_write_text(tmp, f.content)
                    res = self.app.runner.run(["sing-box", "check", "-c", str(tmp)], timeout=60)
                    if not res.ok:
                        return f"Ошибка в конфигурации sing-box:\n{res.output}"
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return None

    def sync_fakesite(self) -> None:
        """Развернуть фейковые сайты из ассетов пакета (обновляются вместе с панелью)."""
        source = resources.files("kvnbox.assets").joinpath("fakesite")
        with resources.as_file(source) as src:
            shutil.copytree(src, self.paths.fakesite_dir, dirs_exist_ok=True)

    # ── внутреннее ───────────────────────────────────────────────────────────

    @property
    def _mita_paths(self) -> set[Path]:
        return {self.paths.mita_config, self.paths.mita_service_override}

    def _restart_core(self, full: bool = False, xray: bool | None = None) -> str | None:
        """`xray`: True — запустить/перезапустить, False — остановить, None — не трогать."""
        if xray is False:
            self.app.systemd.stop(XRAY)
            self.app.systemd.disable(XRAY)
        elif xray:
            ensure_xray_unit(self.paths, self.app.systemd)
            self.app.systemd.enable(XRAY)
            res = self.app.systemd.restart_clean(XRAY)
            if not res.ok:
                return f"Не удалось перезапустить xray: {res.output}"
        # новое окружение службы применяется только полным перезапуском
        res = (
            self.app.systemd.restart_clean(CADDY)
            if full
            else self.app.systemd.reload_or_restart(CADDY)
        )
        if not res.ok:
            return f"Не удалось перезапустить caddy: {res.output}"
        res = self.app.systemd.restart_clean(SINGBOX)
        if not res.ok:
            return f"Не удалось перезапустить sing-box: {res.output}"
        return None

    def _rollback(
        self, plan: RenderPlan, previous: dict[Path, str | None], xray: bool | None = None
    ) -> None:
        for f in plan.files:
            content = previous.get(f.path)
            if content is None:
                f.path.unlink(missing_ok=True)
            else:  # прежнее содержимое с правами и владельцем, которые нужны службе
                write_file(RenderedFile(f.path, content, f.mode, f.owner, f.group))
        self._restart_core(xray=xray)

    def _apply_mita(self, settings: Settings, mita_changed: bool) -> str | None:
        """Служба mita и правила фаервола для порта Mieru (как в TransferBox)."""
        # правила чистим всегда: после удаления mita порт не должен остаться открытым
        # (в TransferBox чистка шла только при установленном mita)
        self.app.ufw.delete_by_comment(*MIERU_RULE_COMMENTS)
        if not self.app.installed("mita"):
            return "Mieru включён, но mita не установлен" if settings.mieru.enabled else None
        if not settings.mieru.enabled:
            self.app.systemd.stop(MITA)
            self.app.systemd.disable(MITA)
            return "Mieru выключен"
        mita_dir = self.paths.mita_config.parent
        (mita_dir / "server.conf.pb").unlink(missing_ok=True)  # иначе mita берёт старый конфиг
        self.app.runner.run(["chown", "-R", "mita:mita", str(mita_dir)], timeout=15)
        if mita_changed:
            self.app.systemd.daemon_reload()
        self.app.systemd.enable(MITA)
        res = self.app.systemd.restart_clean(MITA)
        port = settings.mieru.port
        self.app.ufw.allow(f"{port}/tcp", "Mieru TCP")
        self.app.ufw.allow(f"{port}/udp", "Mieru UDP")
        if not res.ok:
            return f"mita не запустился: {res.output}"
        return f"Mieru: порт {port} открыт, mita перезапущен"


def _changed(plan: RenderPlan, previous: dict[Path, str | None], paths: set[Path]) -> bool:
    files: list[RenderedFile] = [f for f in plan.files if f.path in paths]
    return any(previous.get(f.path) != f.content for f in files)
