"""Главное меню и общий контекст экранов."""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Sequence

from kvnbox.services.app import App
from kvnbox.services.apply import ApplyResult
from kvnbox.tui.header import MAIN_TITLE, HeaderStatus, collect, print_header
from kvnbox.tui.term import DIM, GREEN, RED, RESET, Terminal
from kvnbox.tui.widgets import BACK, ExitMenu, Term, Ui

UPDATE_CACHE_TTL = 3600


class Menu:
    def __init__(self, app: App, term: Term | None = None) -> None:
        self.app = app
        self.ui = Ui(term or Terminal())
        self.status: HeaderStatus | None = None

    # ── общее для экранов ────────────────────────────────────────────────────

    def header(self, title: str, show_status: bool = False) -> None:
        print_header(self.ui, title, self.status, show_status)

    def select(self, title: str, options: Sequence[str]) -> int:
        return self.ui.select_menu(title, options, lambda t: self.header(t, t == MAIN_TITLE))

    def screen(self, title: str) -> None:
        self.ui.clear_screen()
        self.header(title)

    def progress(self, text: str) -> None:
        self.ui.out()
        self.ui.center_print(f"{DIM}[*] {text}{RESET}")

    def ok(self, text: str) -> None:
        self.ui.center_print(f"{GREEN}✓ {text}{RESET}")

    def fail(self, text: str) -> None:
        self.ui.center_print(f"{RED}✗ {text}{RESET}")

    def show_result(self, res: ApplyResult, success: str) -> None:
        self.ui.out()
        if res.ok:
            self.ok(success)
        else:
            for i, line in enumerate(res.message.splitlines()):
                (self.fail if i == 0 else self.ui.center_print)(line)

    # ── запуск ───────────────────────────────────────────────────────────────

    def run(self) -> int:
        self._unban_admin_session()
        with contextlib.suppress(ExitMenu, KeyboardInterrupt):
            self.main_menu()
        self.ui.clear_screen()
        self.ui.t.cursor(True)
        return 0

    def main_menu(self) -> None:
        from kvnbox.tui.screens import backup, diagnostics, modules, settings, users

        while True:
            self.status = collect(self.app)
            self._check_updates_async()
            options = [
                "Пользователи",
                "Настройки",
                "Модули",
                "Диагностика",
                "Бэкап и Восстановление",
                "Выход",
            ]
            choice = self.select(MAIN_TITLE, options)
            if choice == 0:
                users.users_menu(self)
            elif choice == 1:
                settings.settings_menu(self)
            elif choice == 2:
                modules.modules_menu(self)
            elif choice == 3:
                diagnostics.diagnostics_menu(self)
            elif choice == 4:
                backup.backup_restore_menu(self)
            elif choice in (5, BACK):
                return

    def _unban_admin_session(self) -> None:
        """Как в TransferBox: при входе в меню по SSH снять бан с IP администратора
        (защита при динамических IP)."""
        import os

        ip = (os.environ.get("SSH_CLIENT") or "").split()
        if ip and self.app.fail2ban.is_active():
            with contextlib.suppress(Exception):
                self.app.fail2ban.unban(ip[0])

    def _check_updates_async(self) -> None:
        """Как check_updates_async: обновить кэш версии в фоне раз в час (без уведомлений)."""
        cache = self.app.paths.root / ".update_cache"
        try:
            fresh = time.time() - cache.stat().st_mtime < UPDATE_CACHE_TTL
        except OSError:
            fresh = False
        if fresh:
            return

        def work() -> None:
            from kvnbox.services.jobs import Jobs

            with contextlib.suppress(Exception):
                Jobs(self.app).refresh_update_cache()

        threading.Thread(target=work, daemon=True).start()
