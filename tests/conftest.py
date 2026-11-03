from __future__ import annotations

from pathlib import Path

import pytest

from kvnbox.core.models import Settings
from kvnbox.core.paths import Paths


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    return Paths(root=tmp_path / "opt", etc=tmp_path / "etc")


@pytest.fixture
def settings() -> Settings:
    return Settings(domain="tst.example.com")
