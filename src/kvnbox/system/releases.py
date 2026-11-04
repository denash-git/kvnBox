"""Релизы на GitHub и загрузка файлов (deb-пакеты ядер, скрипты установки)."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "kvnBox"

Fetch = Callable[[str, float], bytes]


def http_get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: bytes = resp.read()
        return data


class ReleaseError(Exception):
    pass


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    sha256: str


class NoReleasesError(ReleaseError):
    """Репозиторий доступен, но в нём ещё нет ни одного релиза (GitHub отвечает 404)."""


class Releases:
    def __init__(self, fetch: Fetch = http_get) -> None:
        self.fetch = fetch

    def latest_tag(self, repo: str) -> str:
        """Тег последнего релиза, например «v1.14.4»."""
        try:
            data = json.loads(
                self.fetch(f"https://api.github.com/repos/{repo}/releases/latest", 20)
            )
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NoReleasesError(f"В {repo} нет опубликованных релизов") from e
            raise ReleaseError(f"Не удалось узнать последнюю версию {repo}: {e}") from e
        except Exception as e:  # сеть, лимит API, неверный JSON
            raise ReleaseError(f"Не удалось узнать последнюю версию {repo}: {e}") from e
        tag = data.get("tag_name") if isinstance(data, dict) else None
        if not isinstance(tag, str) or not re.fullmatch(r"v?\d+(\.\d+)*[\w.-]*", tag):
            raise ReleaseError(f"GitHub вернул неожиданный ответ для {repo}")
        return tag

    def latest_version_tag(self, repo: str) -> str:
        """Старший тег вида vX.Y.Z (версии панели — git-теги, D-039)."""
        try:
            data = json.loads(
                self.fetch(f"https://api.github.com/repos/{repo}/tags?per_page=100", 20)
            )
        except Exception as e:  # сеть, лимит API, неверный JSON
            raise ReleaseError(f"Не удалось узнать последнюю версию {repo}: {e}") from e
        if not isinstance(data, list):
            raise ReleaseError(f"GitHub вернул неожиданный ответ для {repo}")
        tags: list[str] = [
            t["name"]
            for t in data
            if isinstance(t, dict)
            and isinstance(t.get("name"), str)
            and re.fullmatch(r"v\d+\.\d+\.\d+", t["name"])
        ]
        if not tags:
            raise NoReleasesError(f"В {repo} нет опубликованных версий")
        return max(tags, key=lambda t: tuple(int(x) for x in t[1:].split(".")))

    def asset(self, repo: str, tag: str, name: str) -> Asset:
        """Файл релиза с контрольной суммой (поле `digest` в API GitHub)."""
        try:
            data = json.loads(
                self.fetch(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", 20)
            )
        except Exception as e:  # сеть, 404 (нет такой версии), неверный JSON
            raise ReleaseError(f"Не удалось получить релиз {repo} {tag}: {e}") from e
        assets = data.get("assets") if isinstance(data, dict) else None
        for a in assets if isinstance(assets, list) else []:
            if not isinstance(a, dict) or a.get("name") != name:
                continue
            url, digest = a.get("browser_download_url"), a.get("digest")
            if not isinstance(url, str) or not isinstance(digest, str):
                break
            sha = digest.removeprefix("sha256:")
            if not re.fullmatch(r"[0-9a-f]{64}", sha):
                break
            return Asset(name=name, url=url, sha256=sha)
        raise ReleaseError(f"В релизе {repo} {tag} нет файла {name} с контрольной суммой")

    def download_verified(self, asset: Asset, dest: Path, timeout: float = 300) -> Path:
        path = self.download(asset.url, dest, timeout)
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        if got != asset.sha256:
            path.unlink(missing_ok=True)
            raise ReleaseError(f"Контрольная сумма {asset.name} не совпала — файл отброшен")
        return path

    def download(self, url: str, dest: Path, timeout: float = 300) -> Path:
        if not url.startswith("https://"):
            raise ReleaseError("Скачивание разрешено только по https")
        try:
            data = self.fetch(url, timeout)
        except Exception as e:
            raise ReleaseError(f"Не удалось скачать {url}: {e}") from e
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest


def version_of(tag: str) -> str:
    return tag.removeprefix("v")
