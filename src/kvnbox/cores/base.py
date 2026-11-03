"""Общее для генераторов конфигов."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from kvnbox.core.models import Connection, CoreKind, User


@dataclass(frozen=True)
class RenderedFile:
    """Файл, который нужно записать: путь, содержимое и права."""

    path: Path
    content: str
    mode: int = 0o600
    owner: str | None = None
    group: str | None = None


def active_connections(users: Iterable[User], core: CoreKind) -> Iterator[tuple[User, Connection]]:
    """Включённые подключения, которые обслуживает ядро `core`, в порядке хранения.

    Точка расширения под выбор ядра: каждое ядро видит только «свои» подключения.
    """
    for user in users:
        for conn in user.connections:
            if conn.enabled and conn.core is core:
                yield user, conn


def load_asset(name: str) -> str:
    return resources.files("kvnbox.assets").joinpath(name).read_text(encoding="utf-8")


_PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")


def fill_template(template: str, values: Mapping[str, str]) -> str:
    """Подставить `{{NAME}}` за один проход.

    Неизвестный плейсхолдер в шаблоне или лишнее значение — ошибка: так опечатка
    не превратится в рабочий, но неверный конфиг. Подставленный текст повторно
    не разбирается.
    """
    used: set[str] = set()

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(f"нет значения для {{{{{name}}}}}")
        used.add(name)
        return values[name]

    result = _PLACEHOLDER.sub(sub, template)
    unused = set(values) - used
    if unused:
        raise KeyError(f"значения не использованы в шаблоне: {sorted(unused)}")
    return result
