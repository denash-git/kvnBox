"""Файловое хранилище без БД: атомарная запись и межпроцессная блокировка.

TUI и Telegram-бот работают одновременно и пишут одни и те же файлы, поэтому:

* запись атомарная — временный файл в той же папке → fsync → `os.replace`;
  при падении посреди записи старый файл остаётся целым;
* изменения делаются под эксклюзивной блокировкой (`flock` на файле `<имя>.lock`),
  `JsonStore.transaction()` держит её на всё «прочитать → изменить → записать»;
* битый файл — это ошибка (`StorageError`), а не «пустой список»: иначе следующее
  сохранение молча стёрло бы всех пользователей.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

if sys.platform != "win32":
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

else:  # Windows — только для локальной разработки и тестов
    import msvcrt

    def _lock_fd(fd: int) -> None:
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.01)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class StorageError(Exception):
    """Файл данных не читается или не проходит валидацию."""


class NotInitializedError(StorageError):
    """Файла данных нет, а значения по умолчанию не предусмотрено (панель не установлена)."""


# Блокировка реентерабельна внутри потока: flock на втором дескрипторе того же
# процесса заблокировал бы сам себя.
_held = threading.local()


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Эксклюзивная межпроцессная блокировка на файле `path`."""
    depth: dict[Path, int] = getattr(_held, "depth", None) or {}
    _held.depth = depth
    key = path.resolve()
    if depth.get(key, 0) > 0:
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        _lock_fd(fd)
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0
            _unlock_fd(fd)
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Атомарно заменить содержимое файла; права выставляются до подмены."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    if sys.platform == "win32":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


M = TypeVar("M", bound=BaseModel)


class JsonStore(Generic[M]):
    """Pydantic-модель, сохранённая в JSON-файле."""

    def __init__(
        self,
        path: Path,
        model: type[M],
        default: Callable[[], M] | None = None,
        mode: int = 0o600,
    ) -> None:
        self.path = path
        self.model = model
        self.default = default
        self.mode = mode
        self.lock_path = path.with_name(path.name + ".lock")

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> M:
        if not self.path.exists():
            if self.default is None:
                raise NotInitializedError(f"Файл данных не найден: {self.path}")
            return self.default()
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as e:
            raise StorageError(f"Не удалось прочитать {self.path}: {e}") from e
        try:
            return self.model.model_validate_json(text)
        except ValidationError as e:
            raise StorageError(f"Повреждённые данные в {self.path}:\n{e}") from e

    def save(self, obj: M) -> None:
        with file_lock(self.lock_path):
            self._write(obj)

    @contextmanager
    def transaction(self) -> Iterator[M]:
        """Прочитать, изменить и записать под одной блокировкой.

        Изменения сохраняются, только если блок завершился без исключения.
        """
        with file_lock(self.lock_path):
            obj = self.load()
            yield obj
            # повторная валидация: изменения через атрибуты pydantic не проверяет
            obj = self.model.model_validate(obj.model_dump())
            self._write(obj)

    def _write(self, obj: M) -> None:
        atomic_write_text(self.path, obj.model_dump_json(indent=2) + "\n", self.mode)
