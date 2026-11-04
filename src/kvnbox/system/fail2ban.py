"""Защита от сканеров: fail2ban (sshd + ловушка портов) и «тихий» сброс.

Ловушка: SYN на типичные порты сканеров логируется правилом в
`/etc/ufw/before*.rules`, jail `portscan-trap` банит источник с первой попытки
на сутки по всем портам (nftables, drop).
"""

from __future__ import annotations

import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kvnbox.core.paths import Paths
from kvnbox.core.storage import atomic_write_text
from kvnbox.system.files import read_text
from kvnbox.system.runner import Runner
from kvnbox.system.systemd import Systemd
from kvnbox.system.ufw import Ufw

TRAP_PORTS = "21,23,25,445,1433,3389,5900,8080"
TRAP_MARK = "[PORTSCAN-TRAP]"

FILTER = """[Definition]
failregex = ^.*\\[PORTSCAN-TRAP\\]: .* SRC=<HOST> .*$
ignoreregex =
"""


def jail_config(ssh_port: int) -> str:
    return f"""[DEFAULT]
bantime = 1d
findtime = 10m
maxretry = 5
backend = systemd
banaction = nftables[type=allports, blocktype=drop]
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
port = {ssh_port}

[portscan-trap]
enabled = true
filter = portscan-trap
backend = systemd
journalmatch = _TRANSPORT=kernel
maxretry = 1
findtime = 1d
bantime = 1d
banaction = nftables[type=allports, blocktype=drop]
"""


def _trap_rule(chain: str) -> str:
    return (
        f"-A {chain} -p tcp -m multiport --dports {TRAP_PORTS} -m tcp --syn "
        f'-m limit --limit 6/min -j LOG --log-prefix "{TRAP_MARK}: " --log-level 4\n'
    )


def with_trap_rules(text: str, chain: str, enable: bool) -> str:
    """Убрать прежние правила ловушки и (если enable) вставить перед первым COMMIT."""
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if TRAP_MARK not in line and line.strip() != "# Portscan Honeypot Trap"
    ]
    if not enable:
        return "".join(lines)
    out, inserted = [], False
    for line in lines:
        if not inserted and line.strip() == "COMMIT":
            out += ["# Portscan Honeypot Trap\n", _trap_rule(chain)]
            inserted = True
        out.append(line)
    if not inserted:
        out.append(_trap_rule(chain))
    return "".join(out)


@dataclass
class JailStatus:
    name: str
    currently_banned: int = 0
    banned_ips: list[str] = field(default_factory=list)


def parse_jail_list(text: str) -> list[str]:
    m = re.search(r"Jail list:\s*(.*)$", text, re.M)
    return [j.strip() for j in m.group(1).split(",") if j.strip()] if m else []


def parse_jail_status(name: str, text: str) -> JailStatus:
    status = JailStatus(name)
    m = re.search(r"Currently banned:\s*(\d+)", text)
    if m:
        status.currently_banned = int(m.group(1))
    m = re.search(r"Banned IP list:\s*(.*)$", text, re.M)
    if m:
        status.banned_ips = m.group(1).split()
    return status


@dataclass
class SecurityStatus:
    active: bool
    jails: list[JailStatus] = field(default_factory=list)
    port80_allowed: bool = False

    @property
    def banned_ips(self) -> list[str]:
        return sorted({ip for j in self.jails for ip in j.banned_ips})


class SecurityError(Exception):
    pass


class Fail2ban:
    def __init__(
        self,
        runner: Runner,
        paths: Paths,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.runner = runner
        self.paths = paths
        self.which = which
        self.systemd = Systemd(runner)
        self.ufw = Ufw(runner)

    def is_installed(self) -> bool:
        return self.which("fail2ban-client") is not None and self.which("ipset") is not None

    def install(self) -> None:
        env = {"DEBIAN_FRONTEND": "noninteractive"}
        self.runner.run(["apt-get", "update", "-qq"], timeout=300, env=env)
        res = self.runner.run(
            ["apt-get", "install", "-y", "-qq", "fail2ban", "ipset", "python3-systemd"],
            timeout=600,
            env=env,
        )
        if not res.ok:
            raise SecurityError(f"Ошибка apt-get install: {res.output}")

    def is_active(self) -> bool:
        return self.systemd.is_active("fail2ban")

    def write_config(self, ssh_port: int) -> None:
        atomic_write_text(self.paths.fail2ban_filter, FILTER, 0o644)
        atomic_write_text(self.paths.fail2ban_jail, jail_config(ssh_port), 0o644)

    def set_trap_rules(self, enable: bool) -> None:
        for path, chain in (
            (self.paths.ufw_before_rules, "ufw-before-input"),
            (self.paths.ufw_before6_rules, "ufw6-before-input"),
        ):
            text = read_text(path)
            if text is not None:
                atomic_write_text(path, with_trap_rules(text, chain, enable), _mode(path))
        self.ufw.reload()

    def close_port80(self) -> None:
        self.ufw.delete_port("80/tcp")

    def enable(self, ssh_port: int) -> None:
        if not self.is_installed():
            self.install()
        self.write_config(ssh_port)
        self.set_trap_rules(True)
        self.close_port80()
        self.systemd.enable("fail2ban")
        res = self.systemd.restart("fail2ban")
        if not res.ok:
            raise SecurityError(f"Не удалось запустить fail2ban: {res.output}")
        # служба уже «active», но сокет клиента появляется позже: без ожидания
        # статус сразу после включения пустой (приёмка, этап 9)
        if not self.wait_ready():
            raise SecurityError("fail2ban запущен, но не отвечает (fail2ban-client ping).")

    def wait_ready(self, timeout: float = 20) -> bool:
        deadline = time.monotonic() + timeout
        while not self.runner.run(["fail2ban-client", "ping"], timeout=5).ok:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)
        return True

    def disable(self) -> None:
        self.unban_all()
        self.set_trap_rules(False)
        self.systemd.stop("fail2ban")
        self.systemd.disable("fail2ban")

    def update_ssh_port(self, ssh_port: int) -> None:
        if self.is_installed() and self.paths.fail2ban_jail.exists():
            self.write_config(ssh_port)
            self.systemd.reload("fail2ban")

    def status(self) -> SecurityStatus:
        st = SecurityStatus(active=self.is_active(), port80_allowed=self.ufw.is_allowed("80/tcp"))
        if not st.active:
            return st
        res = self.runner.run(["fail2ban-client", "status"], timeout=20)
        for name in parse_jail_list(res.stdout):
            j = self.runner.run(["fail2ban-client", "status", name], timeout=20)
            st.jails.append(parse_jail_status(name, j.stdout))
        return st

    def unban(self, ip: str) -> None:
        ip = ip.strip()
        if not re.fullmatch(r"[0-9A-Fa-f:.]+", ip):
            raise SecurityError("Некорректный IP-адрес.")
        res = self.runner.run(["fail2ban-client", "unban", ip], timeout=20)
        if not res.ok:
            raise SecurityError(f"Ошибка разблокировки {ip}: {res.output}")

    def unban_all(self) -> None:
        self.runner.run(["fail2ban-client", "unban", "--all"], timeout=20)


def _mode(path: Path) -> int:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return 0o640
