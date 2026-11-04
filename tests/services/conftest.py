from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from kvnbox.core.models import Settings
from kvnbox.core.paths import Paths
from kvnbox.services.app import App
from kvnbox.system.runner import FakeRunner

DEFAULT_BINARIES = {"caddy", "sing-box", "mita", "fail2ban-client", "ipset", "qrencode", "zip"}


@dataclass
class Env:
    paths: Paths
    runner: FakeRunner
    binaries: set[str] = field(default_factory=lambda: set(DEFAULT_BINARIES))

    def which(self, name: str) -> str | None:
        return f"/usr/bin/{name}" if name in self.binaries else None

    @property
    def app(self) -> App:
        return App(paths=self.paths, runner=self.runner, which=self.which)


@pytest.fixture
def env(paths: Paths, settings: Settings) -> Env:
    e = Env(paths=paths, runner=FakeRunner())
    e.app.settings.save(settings)
    return e
