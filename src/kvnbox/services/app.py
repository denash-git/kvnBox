"""Контейнер зависимостей: один объект на процесс (TUI, CLI или бот)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from kvnbox.core.paths import Paths
from kvnbox.core.repo import SettingsRepo, UsersRepo
from kvnbox.system.bbr import Bbr
from kvnbox.system.fail2ban import Fail2ban
from kvnbox.system.metrics import Metrics
from kvnbox.system.runner import Runner, SubprocessRunner
from kvnbox.system.sshd import Sshd
from kvnbox.system.systemd import Systemd
from kvnbox.system.ufw import Ufw

Which = Callable[[str], "str | None"]


@dataclass
class App:
    paths: Paths
    runner: Runner
    which: Which = field(default=shutil.which)

    def __post_init__(self) -> None:
        self.settings = SettingsRepo(self.paths)
        self.users = UsersRepo(self.paths)
        self.systemd = Systemd(self.runner)
        self.ufw = Ufw(self.runner)
        self.sshd = Sshd(self.runner, self.paths)
        self.fail2ban = Fail2ban(self.runner, self.paths, which=self.which)
        self.bbr = Bbr(self.runner, self.paths)
        self.metrics = Metrics(self.paths)

    @classmethod
    def create(cls) -> App:
        return cls(paths=Paths.from_env(), runner=SubprocessRunner())

    def installed(self, binary: str) -> bool:
        return self.which(binary) is not None
