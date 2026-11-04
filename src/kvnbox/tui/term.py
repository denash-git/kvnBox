"""Терминал: вывод, размеры, чтение клавиш и строк.

Повторяет поведение Bash-меню: `read -rsn1` (одна клавиша без эха),
стрелки — ESC + «[A»/«[B», Enter — пустая строка; `read -r` — ввод строки.
`FakeTerminal` — для тестов: сценарий клавиш и строк, весь вывод в буфер.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import sys
from collections import deque
from collections.abc import Iterable
from typing import Any

BLUE = "\033[1;34m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[1;33m"
CYAN = "\033[1;36m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

UP, DOWN, ENTER, ESC = "UP", "DOWN", "ENTER", "ESC"

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def visible_len(text: str) -> int:
    """Длина как `${#clean_text}` в Bash: символы без цветовых кодов."""
    return len(strip_ansi(text))


class Terminal:
    """Настоящий терминал (stdin/stdout)."""

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def size(self) -> tuple[int, int]:
        s = shutil.get_terminal_size((80, 24))
        return s.columns, s.lines

    def cursor(self, visible: bool) -> None:
        self.write("\033[?25h" if visible else "\033[?25l")

    def read_key(self) -> str:
        """Одна клавиша: UP / DOWN / ENTER / ESC или символ."""
        fd = sys.stdin.fileno()
        with _cbreak(fd):
            ch = os.read(fd, 1)
            if ch == b"\x1b":
                seq = b""
                if select.select([fd], [], [], 0.05)[0]:
                    seq = os.read(fd, 2)
                return {b"[A": UP, b"[B": DOWN}.get(seq, ESC)
            if ch in (b"\r", b"\n"):
                return ENTER
            # многобайтный UTF-8 (например, кириллица) — дочитываем
            first = ch[0]
            extra = 3 if first >= 0xF0 else 2 if first >= 0xE0 else 1 if first >= 0xC0 else 0
            if extra:
                ch += os.read(fd, extra)
            return ch.decode("utf-8", errors="replace")

    def read_line(self) -> str:
        try:
            return input()
        except EOFError:
            return ""

    def wait_input(self, timeout: float) -> str | None:
        """Клавиша, если нажата за `timeout` секунд (для мониторинга), иначе None."""
        fd = sys.stdin.fileno()
        with _cbreak(fd):
            if select.select([fd], [], [], timeout)[0]:
                ch = os.read(fd, 1)
                return ENTER if ch in (b"\r", b"\n") else ch.decode("utf-8", errors="replace")
        return None


class _cbreak:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.saved: Any = None

    def __enter__(self) -> None:
        import termios
        import tty

        self.saved = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)

    def __exit__(self, *exc: object) -> None:
        import termios

        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved)


class FakeTerminal:
    """Терминал для тестов: заранее заданные клавиши/строки, вывод — в `output`."""

    def __init__(self, inputs: Iterable[str] = (), width: int = 100, height: int = 40) -> None:
        self.inputs: deque[str] = deque(inputs)
        self.width, self.height = width, height
        self.output: list[str] = []

    def write(self, text: str) -> None:
        self.output.append(text)

    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def cursor(self, visible: bool) -> None:
        pass

    def _next(self) -> str:
        if not self.inputs:
            raise OutOfInput
        return self.inputs.popleft()

    def read_key(self) -> str:
        return self._next()

    def read_line(self) -> str:
        return self._next()

    def wait_input(self, timeout: float) -> str | None:
        return self._next() if self.inputs else "q"

    @property
    def text(self) -> str:
        return strip_ansi("".join(self.output))

    def screens(self) -> list[str]:
        """Вывод, разбитый по очисткам экрана."""
        return [strip_ansi(s) for s in "".join(self.output).split("\033[2J\033[H")]


class OutOfInput(Exception):
    """Сценарий клавиш в тесте закончился."""
