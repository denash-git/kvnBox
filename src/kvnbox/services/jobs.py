"""Задачи по расписанию (cron): проверка служб и ресурсов, обновлений, автобэкап.

Логика и тексты уведомлений — как в TransferBox (bot/check_services.py,
check_updates.py, backup.py).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import time
from dataclasses import dataclass
from typing import Any

from kvnbox import __version__
from kvnbox.core.storage import atomic_write_text
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.backup import BackupService
from kvnbox.services.notify import NotifyError, Telegram
from kvnbox.system.files import read_text
from kvnbox.system.metrics import CpuTimes, cpu_percent
from kvnbox.system.releases import ReleaseError, Releases, version_of

WATCHED_UNITS = ("caddy", "sing-box", "mita", "netbird")
PANEL_REPO = "denash-git/kvnBox"


@dataclass(frozen=True)
class Threshold:
    key: str
    alert_above: float
    reset_below: float
    alert_text: str
    reset_text: str


THRESHOLDS = (
    Threshold(
        "cpu",
        90,
        80,
        "⚠️ <b>Критическая нагрузка на CPU: {v}%</b>",
        "🟢 <b>Нагрузка на CPU нормализована: {v}%</b>",
    ),
    Threshold(
        "ram",
        90,
        80,
        "⚠️ <b>Критическое потребление памяти RAM: {v}%</b>",
        "🟢 <b>Потребление памяти RAM нормализовано: {v}%</b>",
    ),
    Threshold(
        "disk",
        90,
        85,
        "⚠️ <b>Критическое заполнение диска: {v}%</b>",
        "🟢 <b>Свободное место на диске восстановлено: {v}%</b>",
    ),
)


class Jobs:
    def __init__(
        self, app: App, telegram: Telegram | None = None, releases: Releases | None = None
    ) -> None:
        self.app = app
        self.telegram = telegram or Telegram(app.settings.load().telegram)
        self.releases = releases or Releases()
        self.state_file = app.paths.root / ".service_states"

    # ── проверка служб и ресурсов (каждые 5 минут) ───────────────────────────

    def unit_state(self, unit: str) -> str | None:
        """Состояние службы или None, если её юнита нет (модуль не установлен)."""
        res = self.app.runner.run(["systemctl", "list-unit-files", f"{unit}.service"], timeout=15)
        if f"{unit}.service" not in res.stdout:
            return None
        return self.app.systemd.state(unit)

    def check_services(self) -> list[str]:
        """Возвращает отправленные (или подготовленные) уведомления."""
        old = self._load_state()
        new: dict[str, Any] = {}
        messages: list[str] = []

        for unit in WATCHED_UNITS:
            state = self.unit_state(unit)
            if state is None:
                continue
            new[unit] = state
            if unit in old:
                if old[unit] != state:
                    messages.append(
                        f"🟢 <b>Служба {unit} восстановлена</b>"
                        if state == "active"
                        else f"🔴 <b>Служба {unit} остановлена</b>"
                    )
            elif state != "active":
                messages.append(f"🔴 <b>Служба {unit} не запущена</b>")

        # CPU — средняя нагрузка между запусками (первый раз — замер за 1 с)
        now = self.app.metrics.cpu_times()
        prev = old.get("_cpu_times")
        if isinstance(prev, dict) and now.total > prev.get("total", 0):
            cpu = cpu_percent(CpuTimes(prev["idle"], prev["total"]), now)
        else:
            time.sleep(1)
            cpu = cpu_percent(now, self.app.metrics.cpu_times())
        new["_cpu_times"] = {"idle": now.idle, "total": now.total}

        values = {
            "cpu": float(cpu),
            "ram": self.app.metrics.memory().exact_percent,
            "disk": float(self.app.metrics.disk().percent),
        }
        for t in THRESHOLDS:
            sent = bool(old.get(f"_{t.key}_alert_sent", False))
            v = values[t.key]
            if v > t.alert_above:
                if not sent:
                    messages.append(t.alert_text.format(v=v))
                sent = True
            elif v < t.reset_below and sent:
                messages.append(t.reset_text.format(v=v))
                sent = False
            new[f"_{t.key}_alert_sent"] = sent

        for m in messages:
            self._alert(m)
        atomic_write_text(self.state_file, json.dumps(new, indent=2), 0o600)
        return messages

    def _load_state(self) -> dict[str, Any]:
        try:
            data = json.loads(read_text(self.state_file) or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    # ── проверка обновлений (ежедневно) ──────────────────────────────────────

    def refresh_update_cache(self) -> str | None:
        """Последняя версия панели → `.update_cache` (бейдж в шапке меню)."""
        try:
            latest = version_of(self.releases.latest_tag(PANEL_REPO))
        except ReleaseError:
            return None
        atomic_write_text(self.app.paths.root / ".update_cache", latest + "\n", 0o644)
        return latest

    def check_updates(self) -> str | None:
        """Кэш версии + уведомление о новой версии — один раз на версию."""
        latest = self.refresh_update_cache()
        if latest is None:
            return None
        if latest == __version__:
            return None
        notified_file = self.app.paths.root / ".update_notified"
        if (read_text(notified_file) or "").strip() == latest:
            return None
        text = (
            f"⬆️ <b>Доступно обновление kvnBox!</b>\n\nТекущая версия: <code>{__version__}"
            f"</code>\nСвежая версия: <code>{latest}</code>"
        )
        self._alert(text)
        atomic_write_text(notified_file, latest, 0o644)
        return latest

    # ── автобэкап в Telegram ─────────────────────────────────────────────────

    def backup(self, ignore_enabled: bool = False) -> str:
        settings = self.app.settings.load()
        if not self.telegram.configured:
            raise ServiceError("Токен или Chat ID Telegram не заданы.")
        password = settings.backup.password.strip()
        if not password:
            raise ServiceError("Пароль для бэкапов не задан. Задайте его в настройках.")
        if not ignore_enabled and not settings.backup.enabled:
            raise ServiceError("Автоматические бэкапы отключены в настройках.")
        path = BackupService(self.app).create(password=password)
        try:
            date = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.telegram.send_document(
                path,
                f"💾 <b>Зашифрованный бэкап kvnBox</b>\n\n📅 Дата: <code>{date}</code>\n"
                "🔑 Архив защищен вашим паролем бэкапов.",
            )
        except NotifyError as e:
            raise ServiceError(str(e)) from e
        finally:
            path.unlink(missing_ok=True)  # в TransferBox бэкап из бота на сервере не хранился
        return "Резервная копия успешно отправлена."

    def _alert(self, text: str) -> None:
        with contextlib.suppress(NotifyError):  # алерт не должен ронять задачу cron
            self.telegram.alert(text)
