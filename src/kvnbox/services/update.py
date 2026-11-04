"""Обновление панели: проверка версии; установка обновления — этап 8."""

from __future__ import annotations

import re
from dataclasses import dataclass

from kvnbox import __version__
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.jobs import PANEL_REPO
from kvnbox.system.releases import NoReleasesError, ReleaseError, Releases, version_of


def version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


@dataclass(frozen=True)
class UpdateCheck:
    current: str
    latest: str

    @property
    def up_to_date(self) -> bool:
        return self.latest == self.current

    @property
    def newer_available(self) -> bool:
        return version_key(self.latest) > version_key(self.current)


class UpdateService:
    def __init__(self, app: App, releases: Releases | None = None) -> None:
        self.app = app
        self.releases = releases or Releases()

    def check(self) -> UpdateCheck:
        try:
            latest = version_of(self.releases.latest_tag(PANEL_REPO))
        except NoReleasesError as e:
            raise ServiceError("На GitHub пока нет опубликованных версий kvnBox.") from e
        except ReleaseError as e:
            raise ServiceError("Не удалось подключиться к GitHub для проверки обновлений.") from e
        return UpdateCheck(__version__, latest)

    def update(self, version: str) -> str:
        raise ServiceError("Установка обновлений панели появится на этапе 8 (установщик).")
