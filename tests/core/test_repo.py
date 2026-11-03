from __future__ import annotations

import pytest

from kvnbox.core.models import (
    CoreKind,
    PasswordCredentials,
    Protocol,
    Settings,
    Transport,
    UuidCredentials,
)
from kvnbox.core.paths import Paths
from kvnbox.core.repo import RepoError, SettingsRepo, UsersRepo
from kvnbox.core.storage import NotInitializedError


@pytest.fixture
def users(paths: Paths) -> UsersRepo:
    return UsersRepo(paths)


def test_empty_by_default(users: UsersRepo) -> None:
    assert users.all() == []


def test_add_connection_creates_user(users: UsersRepo) -> None:
    conn = users.add_connection("mama", Protocol.VLESS, Transport.WS)
    assert conn.core is CoreKind.SINGBOX
    assert isinstance(conn.credentials, UuidCredentials)
    user = users.get("mama")
    assert user is not None and user.connections == [conn]


def test_add_connections_share_sub_token(users: UsersRepo) -> None:
    users.add_connection("mama", Protocol.VLESS, Transport.WS)
    token = users.get("mama").sub_token  # type: ignore[union-attr]
    users.add_connection("mama", Protocol.NAIVE)
    users.add_connection("mama", Protocol.MIERU)
    user = users.get("mama")
    assert user is not None
    assert user.sub_token == token
    assert [c.core for c in user.connections] == [CoreKind.SINGBOX, CoreKind.CADDY, CoreKind.MITA]
    for c in user.connections[1:]:
        assert isinstance(c.credentials, PasswordCredentials)
        assert c.credentials.username.startswith("user_")
        assert len(c.credentials.password) == 24


def test_duplicate_slot_message(users: UsersRepo) -> None:
    users.add_connection("papa", Protocol.VLESS, Transport.GRPC)
    with pytest.raises(RepoError, match="VLESS over GRPC"):
        users.add_connection("papa", Protocol.VLESS, Transport.GRPC)
    users.add_connection("papa", Protocol.VLESS, Transport.WS)  # другой транспорт — можно


def test_invalid_name_rejected(users: UsersRepo) -> None:
    for bad in ("Мама", "a b", "UPPER", "x" * 33):
        with pytest.raises(RepoError, match="Имя"):
            users.add_connection(bad, Protocol.NAIVE)


def test_unsupported_core_rejected(users: UsersRepo) -> None:
    with pytest.raises(RepoError, match="не поддерживает"):
        users.add_connection("a", Protocol.NAIVE, core=CoreKind.SINGBOX)
    assert users.all() == []


def test_remove_last_connection_removes_user(users: UsersRepo) -> None:
    users.add_connection("a", Protocol.NAIVE)
    users.add_connection("a", Protocol.VLESS, Transport.WS)
    users.remove_connection("a", Protocol.NAIVE, None)
    assert users.get("a") is not None
    users.remove_connection("a", Protocol.VLESS, Transport.WS)
    assert users.get("a") is None


def test_remove_missing(users: UsersRepo) -> None:
    with pytest.raises(RepoError, match="Пользователь не найден"):
        users.remove_connection("ghost", Protocol.NAIVE, None)
    users.add_connection("a", Protocol.NAIVE)
    with pytest.raises(RepoError, match="Подключение не найдено"):
        users.remove_connection("a", Protocol.MIERU, None)


def test_delete_user(users: UsersRepo) -> None:
    users.add_connection("a", Protocol.NAIVE)
    users.delete_user("a")
    assert users.all() == []


def test_set_enabled_all_and_one(users: UsersRepo) -> None:
    users.add_connection("a", Protocol.NAIVE)
    users.add_connection("a", Protocol.VLESS, Transport.WS)
    users.set_enabled("a", False)
    assert not users.get("a").active  # type: ignore[union-attr]
    users.set_enabled("a", True, Protocol.VLESS, Transport.WS)
    user = users.get("a")
    assert user is not None
    assert [c.enabled for c in user.connections] == [False, True]


def test_settings_repo_requires_install(paths: Paths, settings: Settings) -> None:
    repo = SettingsRepo(paths)
    with pytest.raises(NotInitializedError):
        repo.load()
    repo.save(settings)
    with repo.transaction() as s:
        s.mieru.enabled = True
    assert repo.load().mieru.enabled is True
