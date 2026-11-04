"""Сборка всех конфигов из настроек и пользователей — одна чистая функция.

Результат (`RenderPlan`) записывается на диск, проверяется и применяется
сервисным слоем; здесь нет ни файловых операций, ни вызовов команд.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from kvnbox.clients.subscription import build_subscriptions
from kvnbox.core.models import Settings, User
from kvnbox.core.paths import Paths
from kvnbox.cores import caddy, mita, singbox, xray
from kvnbox.cores.base import RenderedFile


@dataclass(frozen=True)
class RenderPlan:
    files: list[RenderedFile] = field(default_factory=list)
    # содержимое каталога подписок целиком (каталог пересоздаётся)
    subscriptions: list[RenderedFile] = field(default_factory=list)


def render_all(settings: Settings, users: Sequence[User], paths: Paths) -> RenderPlan:
    files = [
        *caddy.render(settings, users, paths),
        *singbox.render(settings, users, paths),
        *xray.render(settings, users, paths),
        *mita.render(settings, users, paths),
    ]
    subscriptions = [
        RenderedFile(paths.sub_dir / token, body, 0o644)
        for token, body in build_subscriptions(users, settings).items()
    ]
    return RenderPlan(files=files, subscriptions=subscriptions)
