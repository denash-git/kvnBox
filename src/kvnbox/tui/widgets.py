"""Элементы экрана — порт функций Bash-меню TransferBox один в один.

clear_screen, pause, get_block_pad, read_prompt, center_print,
center_print_no_newline, draw_line, select_menu.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from kvnbox.tui.term import DIM, DOWN, ENTER, RED, RESET, UP, YELLOW, strip_ansi, visible_len

BLOCK = 60
BACK_LABELS = ("Назад", "Отмена", "Выход")
HINT = f"{DIM}↑↓ — выбор   Enter — выбрать   q — назад   0 — Выход{RESET}"


class Term(Protocol):
    def write(self, text: str) -> None: ...
    def size(self) -> tuple[int, int]: ...
    def cursor(self, visible: bool) -> None: ...
    def read_key(self) -> str: ...
    def read_line(self) -> str: ...
    def wait_input(self, timeout: float) -> str | None: ...


class ExitMenu(Exception):
    pass


BACK = -1  # q в меню


class Ui:
    def __init__(self, term: Term) -> None:
        self.t = term

    @property
    def width(self) -> int:
        return self.t.size()[0]

    def out(self, text: str = "") -> None:
        self.t.write(text + "\n")

    # ── базовые ──────────────────────────────────────────────────────────────

    def clear_screen(self) -> None:
        self.t.write("\033[2J\033[H")

    def center_print(self, text: str) -> None:
        padding = max((self.width - visible_len(text)) // 2, 0)
        self.t.write(" " * padding + text + "\n")

    def center_print_no_newline(self, text: str) -> None:
        padding = max((self.width - visible_len(text)) // 2, 0)
        self.t.write(" " * padding + text)

    def block_pad(self, block_len: int = BLOCK) -> str:
        return " " * max((self.width - block_len) // 2, 2)

    def draw_line(self, char: str, color: str) -> None:
        self.center_print(f"{color}{char * BLOCK}{RESET}")

    def pause(self) -> None:
        self.out()
        self.center_print(f"{DIM}Нажмите любую клавишу для продолжения...{RESET}")
        self.t.read_key()

    def read_prompt(self, prompt: str) -> str:
        self.t.cursor(True)
        self.t.write(self.block_pad() + prompt.lstrip())
        value = self.t.read_line()
        self.t.cursor(False)
        return value

    def ok(self, text: str) -> None:
        self.center_print(f"\033[1;32m✓ {text}{RESET}")

    def fail(self, text: str) -> None:
        self.center_print(f"{RED}✗ {text}{RESET}")

    def info(self, text: str) -> None:
        self.center_print(f"{DIM}{text}{RESET}")

    def confirm(self, prompt: str) -> bool:
        """«(y/N)»: да — только y/д."""
        return self.read_prompt(prompt).strip().lower() in ("y", "д", "yes", "да")

    # ── меню выбора ──────────────────────────────────────────────────────────

    def select_menu(
        self,
        title: str,
        options: Sequence[str],
        header: Callable[[str], None],
    ) -> int:
        """Меню стрелками. Индекс выбранного пункта или BACK (q). «0» — ExitMenu."""
        active = 0
        n = len(options)
        self.t.cursor(False)
        self.clear_screen()
        while True:
            self.t.write("\033[H")
            header(title)
            self._draw_options(options, active)
            self.t.write("\033[J")
            height = self.t.size()[1]
            self.t.write(f"\033[{height};1H\033[K")
            self.center_print_no_newline(HINT)

            key = self.t.read_key()
            if key == UP:
                active = (active - 1 + n) % n
            elif key == DOWN:
                active = (active + 1) % n
            elif key == ENTER:
                self.t.write(f"\033[{height};1H\033[K\033[H")
                self.t.cursor(True)
                return active
            elif key in ("q", "Q"):
                self.t.write(f"\033[{height};1H\033[K\033[H")
                self.t.cursor(True)
                return BACK
            elif key == "0":
                self.clear_screen()
                self.t.cursor(True)
                raise ExitMenu

    def _draw_options(self, options: Sequence[str], active: int) -> None:
        clean = [strip_ansi(o) for o in options]
        max_len = max((len(c) for c in clean), default=0)
        indent = " " * max((self.width - max_len) // 2, 0)
        for i, (opt, plain) in enumerate(zip(options, clean, strict=True)):
            if i:
                self.t.write("\n")
            is_back = plain in BACK_LABELS
            is_danger = "Удалить" in plain or plain in (
                "Пересоздать конфигурации",
                "Отключить Mieru",
            )
            if is_back and i:
                self.t.write("\n")
            if is_back and "\033" not in opt:
                opt = f"{YELLOW}{opt}{RESET}"
            if is_danger and "\033" not in opt:
                opt = f"{RED}{opt}{RESET}"
            gap = max_len - len(plain)
            if is_back:
                left, right = " " * (gap // 2), " " * (gap - gap // 2)
                if i == active:
                    self.t.write(f"{indent}\033[43;30m  {left}{plain}{right}  \033[0m\033[K\n")
                else:
                    self.t.write(f"{indent}  {left}{opt}{right}  \033[K\n")
            else:
                pad = " " * gap
                if i == active:
                    style = "\033[41;30m" if is_danger else "\033[7m"
                    self.t.write(f"{indent}{style}  {plain}{pad}  \033[0m\033[K\n")
                else:
                    self.t.write(f"{indent}  {opt}{pad}  \033[K\n")


__all__ = ["BACK", "ENTER", "ExitMenu", "Ui"]
