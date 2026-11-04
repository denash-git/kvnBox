"""Управление службами systemd и чтение журнала."""

from __future__ import annotations

from kvnbox.system.runner import Result, Runner


class Systemd:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def _ctl(self, *args: str, timeout: float = 90) -> Result:
        return self.runner.run(["systemctl", *args], timeout=timeout)

    def is_active(self, unit: str) -> bool:
        return self._ctl("is-active", "--quiet", unit, timeout=15).ok

    def state(self, unit: str) -> str:
        """active / inactive / failed / activating … (как `systemctl is-active`)."""
        res = self._ctl("is-active", unit, timeout=15)
        out = res.stdout.strip()
        return out or ("active" if res.ok else "inactive")

    def is_enabled(self, unit: str) -> bool:
        return self._ctl("is-enabled", "--quiet", unit, timeout=15).ok

    def start(self, unit: str) -> Result:
        return self._ctl("start", unit)

    def stop(self, unit: str) -> Result:
        return self._ctl("stop", unit)

    def restart(self, unit: str) -> Result:
        return self._ctl("restart", unit)

    def reload(self, unit: str) -> Result:
        return self._ctl("reload", unit)

    def enable(self, unit: str, now: bool = False) -> Result:
        return self._ctl("enable", *(["--now"] if now else []), unit)

    def disable(self, unit: str, now: bool = False) -> Result:
        return self._ctl("disable", *(["--now"] if now else []), unit)

    def reset_failed(self, unit: str) -> Result:
        return self._ctl("reset-failed", unit, timeout=15)

    def daemon_reload(self) -> Result:
        return self._ctl("daemon-reload")

    def restart_clean(self, unit: str) -> Result:
        """reset-failed + restart: сбрасывает «start-limit-hit» после серии сбоев."""
        self.reset_failed(unit)
        return self.restart(unit)

    def reload_or_restart(self, unit: str) -> Result:
        """Мягкая перезагрузка работающей службы, иначе — чистый перезапуск."""
        if self.is_active(unit):
            res = self.reload(unit)
            if res.ok:
                return res
        return self.restart_clean(unit)

    def journal(self, unit: str, lines: int = 100) -> str:
        res = self.runner.run(
            ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "short-iso"],
            timeout=30,
        )
        return res.stdout if res.ok else res.output
