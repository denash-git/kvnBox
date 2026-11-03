"""Запуск внешних команд.

Только список аргументов (никакого `shell=True`), всегда с таймаутом,
stdin закрыт (команда не может зависнуть, ожидая ввода).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        """stderr, а если он пуст — stdout: для сообщений об ошибке."""
        return (self.stderr or self.stdout).strip()


class CommandError(Exception):
    def __init__(self, argv: Sequence[str], result: Result) -> None:
        self.argv = list(argv)
        self.result = result
        super().__init__(f"{' '.join(argv)}: код {result.returncode}: {result.output}")


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        timeout: float = 60,
        env: Mapping[str, str] | None = None,
    ) -> Result: ...


NOT_FOUND = 127
TIMED_OUT = 124


class SubprocessRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        timeout: float = 60,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        full_env = {**os.environ, "LC_ALL": "C", **(env or {})}
        try:
            proc = subprocess.run(
                list(argv),
                input=input,
                stdin=None if input is not None else subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=full_env,
                check=False,
            )
        except FileNotFoundError:
            return Result(NOT_FOUND, "", f"команда не найдена: {argv[0]}")
        except subprocess.TimeoutExpired:
            return Result(TIMED_OUT, "", f"превышено время ожидания ({timeout:g} с): {argv[0]}")
        return Result(proc.returncode, proc.stdout, proc.stderr)


def check(
    runner: Runner, argv: Sequence[str], *, input: str | None = None, timeout: float = 60
) -> Result:
    """Выполнить и бросить `CommandError`, если код возврата не 0."""
    result = runner.run(argv, input=input, timeout=timeout)
    if not result.ok:
        raise CommandError(argv, result)
    return result


# ─── для тестов ──────────────────────────────────────────────────────────────

Responder = Callable[[list[str], str | None], Result]


@dataclass
class FakeRunner:
    """Подменный Runner: ответы по префиксу команды, журнал всех вызовов.

    `responses` — список (префикс argv, Result или функция). Берётся первый
    подходящий; если не нашлось — `default`.
    """

    responses: list[tuple[tuple[str, ...], Result | Responder]] = field(default_factory=list)
    default: Result = field(default_factory=lambda: Result(0))
    calls: list[list[str]] = field(default_factory=list)
    inputs: list[str | None] = field(default_factory=list)

    def on(self, *prefix: str, result: Result | Responder) -> FakeRunner:
        self.responses.append((prefix, result))
        return self

    def run(
        self,
        argv: Sequence[str],
        *,
        input: str | None = None,
        timeout: float = 60,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        args = list(argv)
        self.calls.append(args)
        self.inputs.append(input)
        for prefix, result in self.responses:
            if tuple(args[: len(prefix)]) == prefix:
                return result(args, input) if callable(result) else result
        return self.default

    def called(self, *prefix: str) -> bool:
        return any(tuple(c[: len(prefix)]) == prefix for c in self.calls)
