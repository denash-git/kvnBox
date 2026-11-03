"""Golden-тесты: kvnBox генерирует то же, что TransferBox v3.0.9 (D-019).

Эталоны сняты со стенда старым кодом (tests/fixtures/transferbox/<вариант>).
Подписки и ссылки — байт-в-байт; basic_auth в Caddyfile и пользователи inbound
sing-box — без учёта порядка (в TransferBox он глобальный по файлу записей
и на работу не влияет).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from kvnbox.clients import links
from kvnbox.clients.subscription import build_subscriptions
from kvnbox.core.models import Protocol, Settings, UsersDB
from kvnbox.core.paths import Paths
from kvnbox.cores import caddy, mita, singbox
from tests.legacy import TRANSPORTS, load_transferbox

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transferbox"
VARIANTS = ["default", "custom", "real"]
PATHS = Paths()  # /opt/kvnbox, /etc


def _expected(variant: str, name: str) -> str:
    text = (FIXTURES / variant / name).read_text(encoding="utf-8")
    # пути TransferBox → пути kvnBox
    return text.replace("/opt/transferbox/templates/fakesite", "/opt/kvnbox/fakesite").replace(
        "/opt/transferbox/sub", "/opt/kvnbox/sub"
    )


def _sort_basic_auth(text: str) -> str:
    lines = text.split("\n")
    idx = [i for i, line in enumerate(lines) if line.strip().startswith("basic_auth ")]
    for i, line in zip(idx, sorted(lines[i] for i in idx), strict=True):
        lines[i] = line
    return "\n".join(lines)


def _sort_users(config: dict[str, Any], key: str) -> dict[str, Any]:
    for inbound in config.get("inbounds", []):
        inbound["users"] = sorted(inbound["users"], key=lambda u: u[key])
    if "users" in config:
        config["users"] = sorted(config["users"], key=lambda u: u[key])
    return config


@pytest.fixture(params=VARIANTS)
def variant(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def data(variant: str) -> tuple[Settings, UsersDB]:
    return load_transferbox(FIXTURES / variant)


def test_caddyfile(variant: str, data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    actual = caddy.render_caddyfile(settings, db.users, PATHS)
    assert _sort_basic_auth(actual) == _sort_basic_auth(_expected(variant, "Caddyfile"))


def test_singbox(variant: str, data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    actual = singbox.build_config(settings, db.users)
    expected = json.loads(_expected(variant, "sing-box.json"))
    assert _sort_users(actual, "uuid") == _sort_users(expected, "uuid")


def test_mita(variant: str, data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    assert settings.mieru.enabled  # во всех эталонах Mieru включён
    expected = json.loads(_expected(variant, "mita.json"))
    assert _sort_users(mita.build_config(settings, db.users), "name") == _sort_users(
        expected, "name"
    )
    assert mita.service_override() == _expected(variant, "mita-override.conf")


def test_subscriptions(variant: str, data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    expected = {
        f.name: f.read_text(encoding="utf-8") for f in (FIXTURES / variant / "sub").iterdir()
    }
    assert build_subscriptions(db.users, settings) == expected


def test_links(variant: str, data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    records = json.loads((FIXTURES / variant / "links.json").read_text(encoding="utf-8"))
    assert records
    for rec in records:
        user = db.get(rec["nickname"])
        assert user is not None
        protocol = Protocol(rec["protocol"])
        transport = TRANSPORTS[rec["type"]] if protocol is Protocol.VLESS else None
        conn = user.find(protocol, transport)
        assert conn is not None
        assert links.build_link(user, conn, settings) == rec["link"]
        if protocol is Protocol.MIERU:
            assert links.mieru_client_json(user, conn, settings) == rec["mieru_json"]
            assert links.mieru_clash_yaml(user, conn, settings) == rec["mieru_clash"]
            assert links.mieru_singbox_json(user, conn, settings) == rec["mieru_singbox"]


def test_fixtures_cover_all_slots() -> None:
    """Эталоны покрывают все протоколы/транспорты и отключённые подключения."""
    slots, disabled = set(), 0
    for v in VARIANTS:
        _, db = load_transferbox(FIXTURES / v)
        for u in db.users:
            for c in u.connections:
                slots.add(c.slot)
                disabled += not c.enabled
    assert len(slots) == 5
    assert disabled > 0


def test_no_unknown_placeholders_left(data: tuple[Settings, UsersDB]) -> None:
    settings, db = data
    text = caddy.render_caddyfile(settings, db.users, PATHS)
    assert not re.search(r"\{\{[A-Z_]+\}\}", text)
