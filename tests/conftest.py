from __future__ import annotations

from pathlib import Path

import pytest

from kvnbox.core.models import Settings
from kvnbox.core.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    # все корни — во временном каталоге: тест не должен трогать настоящую систему
    return Paths(
        root=tmp_path / "opt", etc=tmp_path / "etc", proc=tmp_path / "proc", home=tmp_path / "home"
    )


@pytest.fixture
def settings() -> Settings:
    return Settings(domain="tst.example.com")
