"""Обновление панели: проверка версии по git-тегам и установка новой версии (D-039).

Устройство на сервере (его создаёт install.sh):

    /opt/kvnbox/releases/<версия>/   распакованный архив тега (src/, requirements.lock, …)
    /opt/kvnbox/app -> releases/<версия>
    /opt/kvnbox/.venv                .pth указывает на /opt/kvnbox/app/src

Обновление: скачать архив тега → зависимости по хэшам → снимок data/ →
переключить ссылку → `kvnbox apply` уже новой версией (отдельным процессом).
Ошибка — ссылка и data/ возвращаются, конфиги пересобираются старой версией.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kvnbox import __version__
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.jobs import PANEL_REPO
from kvnbox.system.releases import NoReleasesError, ReleaseError, Releases, version_of

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
BOT_UNIT = "kvnbox-bot"


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


def archive_url(version: str) -> str:
    return f"https://github.com/{PANEL_REPO}/archive/refs/tags/v{version}.tar.gz"


def read_version(release: Path) -> str:
    """`__version__` из распакованной версии (без импорта её кода)."""
    text = (release / "src" / "kvnbox" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'^__version__ = "([^"]+)"', text, re.M)
    return m.group(1) if m else ""


class UpdateService:
    def __init__(self, app: App, releases: Releases | None = None) -> None:
        self.app = app
        self.releases = releases or Releases()

    def check(self) -> UpdateCheck:
        try:
            latest = version_of(self.releases.latest_version_tag(PANEL_REPO))
        except NoReleasesError as e:
            raise ServiceError("На GitHub пока нет опубликованных версий kvnBox.") from e
        except ReleaseError as e:
            raise ServiceError("Не удалось подключиться к GitHub для проверки обновлений.") from e
        return UpdateCheck(__version__, latest)

    # ── установка ────────────────────────────────────────────────────────────

    def update(self, version: str) -> str:
        """Поставить версию `version`. Сообщение для пользователя или ServiceError."""
        p = self.app.paths
        if not VERSION_RE.fullmatch(version):
            raise ServiceError(f"Неверный номер версии: {version}")
        if not p.app_link.is_symlink():
            raise ServiceError("Панель установлена не через install.sh — обновление недоступно.")
        old_release = Path(os.readlink(p.app_link))
        if not old_release.is_absolute():
            old_release = p.app_link.parent / old_release
        new_release = p.releases_dir / version
        if new_release == old_release:
            raise ServiceError(f"Версия {version} уже установлена.")

        self._fetch(version, new_release)
        try:
            self._install_requirements(new_release)
        except ServiceError:
            shutil.rmtree(new_release, ignore_errors=True)
            raise
        snapshot = self._snapshot_data()
        self._switch(new_release)
        res = self.app.runner.run([self._python(), "-m", "kvnbox", "apply"], timeout=300)
        if not res.ok:
            self._switch(old_release)
            self._restore_data(snapshot)
            self.app.runner.run([self._python(), "-m", "kvnbox", "apply"], timeout=300)
            shutil.rmtree(new_release, ignore_errors=True)
            raise ServiceError(
                f"Версия {version} не запустилась, возвращена прежняя ({old_release.name}).\n"
                f"{res.output}"
            )
        self._cleanup(keep={new_release, old_release})  # предыдущая — для отката
        (p.root / ".update_cache").unlink(missing_ok=True)
        return f"Обновление до версии {version} успешно завершено!"

    def restart_bot(self) -> None:
        """Перезапустить бота новой версией (без ожидания — бот может вызывать это сам)."""
        if self.app.systemd.state(BOT_UNIT) == "active":
            self.app.runner.run(["systemctl", "--no-block", "restart", BOT_UNIT], timeout=30)

    def _python(self) -> str:
        return (self.app.paths.venv / "bin" / "python").as_posix()

    def _fetch(self, version: str, dest: Path) -> None:
        p = self.app.paths
        p.releases_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=p.releases_dir, prefix=".download-") as tmp:
            try:
                archive = self.releases.download(archive_url(version), Path(tmp) / "src.tar.gz")
            except ReleaseError as e:
                raise ServiceError(f"Не удалось скачать версию {version}: {e}") from e
            stage = Path(tmp) / "x"
            try:
                with tarfile.open(archive) as tf:
                    tf.extractall(stage, filter="data")
            except (tarfile.TarError, OSError) as e:
                raise ServiceError(f"Архив версии {version} повреждён: {e}") from e
            tops = [d for d in stage.iterdir() if d.is_dir()]
            if len(tops) != 1 or read_version_safe(tops[0]) != version:
                raise ServiceError(f"Архив не содержит kvnBox {version}.")
            if dest.exists():
                shutil.rmtree(dest)
            shutil.rmtree(tops[0] / "tests", ignore_errors=True)  # на сервере не нужны
            shutil.move(str(tops[0]), dest)

    def _install_requirements(self, release: Path) -> None:
        lock = release / "requirements.lock"
        if not lock.exists():
            raise ServiceError("В новой версии нет requirements.lock.")
        res = self.app.runner.run(
            [
                (self.app.paths.venv / "bin" / "pip").as_posix(),
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--require-hashes",
                "-r",
                lock.as_posix(),
            ],
            timeout=900,
        )
        if not res.ok:
            raise ServiceError(f"Не удалось установить зависимости новой версии:\n{res.output}")

    def _snapshot_data(self) -> Path:
        p = self.app.paths
        snapshot = p.root / ".update_snapshot"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        shutil.copytree(p.data_dir, snapshot)
        return snapshot

    def _restore_data(self, snapshot: Path) -> None:
        p = self.app.paths
        shutil.rmtree(p.data_dir)
        shutil.copytree(snapshot, p.data_dir)

    def _switch(self, release: Path) -> None:
        """Атомарно перенаправить `app` на `release`."""
        p = self.app.paths
        tmp = p.root / ".app.new"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(release)
        os.replace(tmp, p.app_link)

    def _cleanup(self, keep: set[Path]) -> None:
        releases = sorted(
            (d for d in self.app.paths.releases_dir.iterdir() if d.is_dir() and d not in keep),
            key=lambda d: version_key(d.name),
        )
        for d in releases:
            if not d.name.startswith("."):
                shutil.rmtree(d, ignore_errors=True)


def read_version_safe(release: Path) -> str:
    try:
        return read_version(release)
    except OSError:
        return ""
