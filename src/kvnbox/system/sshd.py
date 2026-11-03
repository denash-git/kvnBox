"""SSH-сервер: порт, вход по паролю/ключу, authorized_keys.

Фактические значения берутся из `sshd -T` — он учитывает drop-in'ы
(`sshd_config.d/*.conf`), в том числе файлы провайдера VPS.
"""

from __future__ import annotations

import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.runner import Result, Runner
from kvnbox.system.systemd import Systemd

AuthMode = Literal["password", "key"]

INCLUDE_LINE = "Include /etc/ssh/sshd_config.d/*.conf"

_AUTH_DROPIN = {
    "key": (
        "# kvnBox SSH Authentication\n"
        "PasswordAuthentication no\n"
        "KbdInteractiveAuthentication no\n"
        "PubkeyAuthentication yes\n"
        "AuthorizedKeysFile .ssh/authorized_keys\n"
    ),
    "password": (
        "# kvnBox SSH Authentication\n"
        "PasswordAuthentication yes\n"
        "KbdInteractiveAuthentication yes\n"
        "PubkeyAuthentication yes\n"
        "AuthorizedKeysFile .ssh/authorized_keys\n"
    ),
}


class SshError(Exception):
    pass


@dataclass(frozen=True)
class AuthorizedKey:
    index: int  # с 1, в порядке файла
    type: str
    fingerprint: str
    comment: str
    raw: str


def parse_sshd_T(text: str) -> dict[str, list[str]]:
    """Вывод `sshd -T`: ключ (в нижнем регистре) → все значения (Port бывает несколько)."""
    result: dict[str, list[str]] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            result.setdefault(parts[0].lower(), []).append(parts[1])
    return result


def parse_ports_from_config(text: str) -> list[int]:
    ports = []
    for line in text.splitlines():
        m = re.match(r"^\s*Port\s+(\d+)\s*$", line, re.I)
        if m:
            ports.append(int(m.group(1)))
    return ports


def parse_auth_dropin(text: str) -> AuthMode | None:
    if re.search(r"^\s*PasswordAuthentication\s+no\b", text, re.M | re.I):
        return "key"
    if re.search(r"^\s*PasswordAuthentication\s+yes\b", text, re.M | re.I):
        return "password"
    return None


class Sshd:
    def __init__(self, runner: Runner, paths: Paths) -> None:
        self.runner = runner
        self.paths = paths
        self.systemd = Systemd(runner)

    # ── чтение ───────────────────────────────────────────────────────────────

    def effective(self) -> dict[str, list[str]] | None:
        res = self.runner.run(["sshd", "-T"], timeout=15)
        return parse_sshd_T(res.stdout) if res.ok else None

    def port(self) -> int:
        eff = self.effective()
        if eff and eff.get("port"):
            return int(eff["port"][0])
        ports = parse_ports_from_config(read_text(self.paths.sshd_config) or "")
        return ports[0] if ports else 22

    def auth_mode(self) -> AuthMode:
        eff = self.effective()
        if eff and eff.get("passwordauthentication"):
            return "password" if eff["passwordauthentication"][0] == "yes" else "key"
        mode = parse_auth_dropin(read_text(self.paths.ssh_auth_dropin) or "")
        return mode or "password"

    def uses_socket_activation(self) -> bool:
        """Ubuntu 22.10+ запускает sshd через ssh.socket — порт задаёт сокет."""
        return self.systemd.is_enabled("ssh.socket") or self.systemd.is_active("ssh.socket")

    # ── применение ───────────────────────────────────────────────────────────

    def test_config(self) -> Result:
        return self.runner.run(["sshd", "-t"], timeout=15)

    def restart(self) -> Result:
        if self.uses_socket_activation():
            self.systemd.daemon_reload()
            res = self.systemd.restart("ssh.socket")
            if not res.ok:
                return res
        res = self.systemd.restart("ssh")
        return res if res.ok else self.systemd.restart("sshd")

    def ensure_include(self) -> None:
        text = read_text(self.paths.sshd_config)
        if text is not None and INCLUDE_LINE.lower() not in text.lower():
            atomic_write_text(self.paths.sshd_config, f"{INCLUDE_LINE}\n{text}", 0o644)

    def _apply_with_rollback(self, path: Path, content: str | None, mode: int = 0o644) -> None:
        """Записать (или удалить при None) файл конфигурации sshd; при ошибке
        `sshd -t` или перезапуска — вернуть прежнее содержимое и перезапустить."""
        before = read_text(path)
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, content, mode)
        test = self.test_config()
        if test.ok:
            res = self.restart()
            if res.ok:
                return
            err = f"не удалось перезапустить SSH: {res.output}"
        else:
            err = f"конфигурация SSH некорректна: {test.output}"
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_text(path, before, mode)
        self.restart()
        raise SshError(err)

    def set_auth_mode(self, mode: AuthMode) -> None:
        if mode == "key" and not self.authorized_keys():
            raise SshError(
                "Нельзя отключить вход по паролю: нет ни одного открытого ключа. "
                "Сначала добавьте или сгенерируйте ключ."
            )
        self.ensure_include()
        self.paths.sshd_dropin_dir.mkdir(parents=True, exist_ok=True)
        self._apply_with_rollback(self.paths.ssh_auth_dropin, _AUTH_DROPIN[mode])

    def set_port(self, new_port: int) -> None:
        """Сменить порт в sshd_config (фаервол и fail2ban — забота вызывающего)."""
        if not 1 <= new_port <= 65535:
            raise SshError("Неверный порт. Диапазон: 1-65535.")
        text = read_text(self.paths.sshd_config)
        if text is None:
            raise SshError(f"Не найден {self.paths.sshd_config}")
        lines = text.splitlines(keepends=True)
        replaced = False
        for i, line in enumerate(lines):
            if re.match(r"^\s*#?\s*Port\s+\d+\s*$", line, re.I) and not replaced:
                lines[i] = f"Port {new_port}\n"
                replaced = True
            elif re.match(r"^\s*Port\s+\d+\s*$", line, re.I):
                lines[i] = ""  # остальные Port — убрать, иначе sshd слушает несколько
        if not replaced:
            lines.append(f"\nPort {new_port}\n")
        self._apply_with_rollback(self.paths.sshd_config, "".join(lines))
        if self.port() != new_port or not self.is_listening(new_port):
            self._apply_with_rollback(self.paths.sshd_config, text)
            raise SshError(f"SSH не запустился на порту {new_port}. Изменения отменены.")

    def is_listening(self, port: int) -> bool:
        res = self.runner.run(["ss", "-Htln"], timeout=15)
        return bool(re.search(rf"[:\]]{port}\s", res.stdout)) if res.ok else False

    # ── ключи ────────────────────────────────────────────────────────────────

    def authorized_keys(self) -> list[AuthorizedKey]:
        text = read_text(self.paths.authorized_keys) or ""
        keys: list[AuthorizedKey] = []
        for line in text.splitlines():
            raw = line.strip()
            parts = raw.split()
            if not raw or raw.startswith("#") or len(parts) < 2:
                continue
            keys.append(
                AuthorizedKey(
                    index=len(keys) + 1,
                    type=parts[0],
                    fingerprint=self._fingerprint(raw) or f"{parts[1][:12]}...{parts[1][-8:]}",
                    comment=" ".join(parts[2:]) or "без комментария",
                    raw=raw,
                )
            )
        return keys

    def _fingerprint(self, raw: str) -> str:
        res = self.runner.run(["ssh-keygen", "-lf", "-"], input=raw + "\n", timeout=10)
        parts = res.stdout.split()
        return parts[1] if res.ok and len(parts) >= 2 else ""

    def add_key(self, public_key: str) -> None:
        line = public_key.strip()
        if len(line.split()) < 2 or "\n" in line:
            raise SshError("Это не похоже на открытый ключ SSH.")
        if not self._fingerprint(line):
            raise SshError("ssh-keygen не распознал ключ.")
        if any(k.raw == line for k in self.authorized_keys()):
            raise SshError("Такой ключ уже добавлен.")
        current = read_text(self.paths.authorized_keys) or ""
        if current and not current.endswith("\n"):
            current += "\n"
        ssh_dir = self.paths.authorized_keys.parent
        ssh_dir.mkdir(parents=True, exist_ok=True)
        ssh_dir.chmod(0o700)
        atomic_write_text(self.paths.authorized_keys, current + line + "\n", 0o600)

    def delete_key(self, index: int) -> tuple[AuthorizedKey, bool]:
        """Удалить ключ по номеру. Если удалён последний ключ при входе только по
        ключу — вход по паролю включается автоматически (как в TransferBox), иначе
        сервер стал бы недоступен. Возвращает (ключ, был_ли_включён_пароль)."""
        keys = self.authorized_keys()
        if not 1 <= index <= len(keys):
            raise SshError(f"Неверный номер ключа: {index}.")
        last_key_in_key_mode = len(keys) == 1 and self.auth_mode() == "key"
        if last_key_in_key_mode:
            self.set_auth_mode("password")  # сначала пароль, потом удаление ключа
        target = keys[index - 1]
        text = read_text(self.paths.authorized_keys) or ""
        out, removed = [], False
        for line in text.splitlines(keepends=True):
            if not removed and line.strip() == target.raw:
                removed = True
                continue
            out.append(line)
        atomic_write_text(self.paths.authorized_keys, "".join(out), 0o600)
        return target, last_key_in_key_mode

    def generate_key(self) -> Path:
        """Создать пару ed25519 в домашнем каталоге root, открытую часть — в
        authorized_keys. Возвращает путь к закрытому ключу (его забирает владелец)."""
        host = (socket.gethostname().split(".")[0] or "server").replace("/", "_")
        n = 1
        while (self.paths.home / f"ssh_key_kvnbox_{host}_{n}").exists():
            n += 1
        private = self.paths.home / f"ssh_key_kvnbox_{host}_{n}"
        res = self.runner.run(
            [
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                f"kvnbox_{host}_{n}",
                "-f",
                str(private),
            ],
            timeout=30,
        )
        if not res.ok:
            raise SshError(f"Ошибка ssh-keygen: {res.output}")
        pub = private.with_name(private.name + ".pub")
        self.add_key(pub.read_text(encoding="utf-8"))
        pub.unlink(missing_ok=True)
        private.chmod(0o600)
        return private
