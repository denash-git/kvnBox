"""Запись сгенерированных файлов на диск."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from kvnbox.core.storage import atomic_write_text
from kvnbox.cores.base import RenderedFile


def _chown(path: Path, owner: str | None, group: str | None) -> None:
    if sys.platform == "win32" or (owner is None and group is None):
        return
    import grp
    import pwd

    try:
        uid = pwd.getpwnam(owner).pw_uid if owner else -1
        gid = grp.getgrnam(group).gr_gid if group else -1
    except KeyError:
        # пользователя/группы ещё нет (пакет не установлен) — оставляем root
        return
    os.chown(path, uid, gid)


def write_file(f: RenderedFile) -> None:
    atomic_write_text(f.path, f.content, f.mode)
    _chown(f.path, f.owner, f.group)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def replace_directory(directory: Path, files: Sequence[RenderedFile], mode: int = 0o755) -> None:
    """Заменить содержимое каталога целиком.

    Новый каталог собирается рядом и подменяется двумя `rename`, поэтому Caddy
    не увидит момент, когда файлов подписок нет.
    """
    parent = directory.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=parent, prefix=f".{directory.name}.new."))
    try:
        for f in files:
            if f.path.parent != directory:
                raise ValueError(f"{f.path} не лежит в {directory}")
            write_file(RenderedFile(staging / f.path.name, f.content, f.mode, f.owner, f.group))
        os.chmod(staging, mode)
        old = parent / f".{directory.name}.old"
        if old.exists():
            shutil.rmtree(old)
        if directory.exists():
            os.replace(directory, old)
        os.replace(staging, directory)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
