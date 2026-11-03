from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest
from pydantic import BaseModel

from kvnbox.core.storage import (
    JsonStore,
    NotInitializedError,
    StorageError,
    atomic_write_text,
    file_lock,
)


class Counter(BaseModel):
    value: int = 0
    items: list[int] = []


def _store(path: Path) -> JsonStore[Counter]:
    return JsonStore(path, Counter, default=Counter)


def test_load_default_when_missing(tmp_path: Path) -> None:
    assert _store(tmp_path / "c.json").load() == Counter()


def test_load_without_default_raises(tmp_path: Path) -> None:
    with pytest.raises(NotInitializedError):
        JsonStore(tmp_path / "c.json", Counter).load()


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path / "sub" / "c.json")
    store.save(Counter(value=5, items=[1, 2]))
    assert store.load() == Counter(value=5, items=[1, 2])


def test_corrupted_file_is_error_not_empty(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text("{ broken", encoding="utf-8")
    with pytest.raises(StorageError):
        _store(path).load()


def test_invalid_data_is_error(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text('{"value": "not-int"}', encoding="utf-8")
    with pytest.raises(StorageError):
        _store(path).load()


def test_transaction_saves_on_success(tmp_path: Path) -> None:
    store = _store(tmp_path / "c.json")
    with store.transaction() as c:
        c.value = 7
    assert store.load().value == 7


def test_transaction_discards_on_error(tmp_path: Path) -> None:
    store = _store(tmp_path / "c.json")
    store.save(Counter(value=1))
    with pytest.raises(RuntimeError), store.transaction() as c:
        c.value = 99
        raise RuntimeError("boom")
    assert store.load().value == 1


def test_atomic_write_keeps_old_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "f.txt"
    atomic_write_text(path, "old")

    def fail(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write_text(path, "new")
    assert path.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]  # временный файл убран


@pytest.mark.skipif(os.name != "posix", reason="права файлов — только POSIX")
def test_atomic_write_sets_mode(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    atomic_write_text(path, "x", mode=0o600)
    assert path.stat().st_mode & 0o777 == 0o600


def test_file_lock_is_reentrant(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with file_lock(lock), file_lock(lock):
        pass
    with file_lock(lock):  # после выхода блокировка снова берётся
        pass


def test_nested_transactions_same_store(tmp_path: Path) -> None:
    store = _store(tmp_path / "c.json")
    with store.transaction() as outer:
        outer.value = 1
        store.save(Counter(value=2))  # не должно зависнуть
    assert store.load().value == 1


def _increment(path_str: str, n: int) -> None:
    store = _store(Path(path_str))
    for _ in range(n):
        with store.transaction() as c:
            c.value += 1
            c.items.append(os.getpid())


def test_concurrent_processes_do_not_lose_updates(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    workers, per_worker = 4, 25
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_increment, args=(str(path), per_worker)) for _ in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
        assert p.exitcode == 0
    result = _store(path).load()
    assert result.value == workers * per_worker
    assert len(result.items) == workers * per_worker
