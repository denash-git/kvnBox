"""Парсеры на реальных выводах команд со стенда (Debian 13), обезличенных."""

from __future__ import annotations

from pathlib import Path

from kvnbox.system.fail2ban import parse_jail_list, parse_jail_status
from kvnbox.system.metrics import (
    CpuTimes,
    cpu_percent,
    format_uptime,
    parse_cpu,
    parse_default_iface,
    parse_meminfo,
    parse_net_dev,
)
from kvnbox.system.sshd import parse_auth_dropin, parse_ports_from_config, parse_sshd_T
from kvnbox.system.ufw import parse_status_numbered

FIX = Path(__file__).parent.parent / "fixtures" / "system"


def fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_ufw_status_numbered() -> None:
    active, rules = parse_status_numbered(fx("ufw_status_numbered.txt"))
    assert active
    assert [r.num for r in rules] == list(range(1, 11))
    mieru = [r for r in rules if r.comment == "Mieru TCP"]
    assert [(r.port_proto, r.v6) for r in mieru] == [("21000/tcp", False), ("21000/tcp", True)]
    assert rules[0].to == "22/tcp" and rules[0].action == "ALLOW" and rules[0].source == "Anywhere"
    assert rules[5].to == "22/tcp (v6)" and rules[5].source == "Anywhere (v6)"


def test_ufw_inactive() -> None:
    assert parse_status_numbered("Status: inactive\n") == (False, [])


def test_fail2ban() -> None:
    assert parse_jail_list(fx("f2b_status.txt")) == ["portscan-trap", "sshd"]
    sshd = parse_jail_status("sshd", fx("f2b_status_sshd.txt"))
    assert sshd.currently_banned == 8 and len(sshd.banned_ips) == 8
    assert all(ip.startswith("203.0.113.") for ip in sshd.banned_ips)
    trap = parse_jail_status("portscan-trap", fx("f2b_status_trap.txt"))
    assert trap.currently_banned == len(trap.banned_ips)


def test_sshd_T() -> None:
    eff = parse_sshd_T(fx("sshd_T.txt"))
    assert eff["port"] == ["22"]
    assert eff["passwordauthentication"] == ["yes"]


def test_sshd_config_ports_and_dropin() -> None:
    assert parse_ports_from_config("#Port 22\nPort 2222\n port 2223 \n") == [2222, 2223]
    assert parse_auth_dropin("PasswordAuthentication no\n") == "key"
    assert parse_auth_dropin("# x\npasswordauthentication YES\n") == "password"
    assert parse_auth_dropin("") is None


def test_meminfo() -> None:
    mem = parse_meminfo(fx("meminfo.txt"))
    assert 900 < mem.total_mb < 1100  # стенд 1 ГБ
    assert 0 < mem.used_mb < mem.total_mb
    assert 0 <= mem.percent <= 100


def test_cpu() -> None:
    t = parse_cpu(fx("stat_cpu.txt"))
    assert t.total > t.idle > 0
    assert cpu_percent(CpuTimes(100, 200), CpuTimes(150, 300)) == 50
    assert cpu_percent(t, t) == 0


def test_net_dev() -> None:
    text = fx("net_dev.txt")
    lo = parse_net_dev(text, "lo")
    assert lo is not None and lo.rx_bytes >= 0
    assert parse_net_dev(text, "nope0") is None


def test_default_iface() -> None:
    route = (
        "Iface\tDestination\tGateway\tFlags\n"
        "ens3\t00000000\t0101A8C0\t0003\n"
        "ens3\t0001A8C0\t00000000\t0001\n"
    )
    assert parse_default_iface(route) == "ens3"
    assert parse_default_iface("Iface\tDestination\n") is None


def test_format_uptime() -> None:
    assert format_uptime(59) == "0 мин"
    assert format_uptime(3 * 86400 + 2 * 3600 + 5 * 60) == "3 д 2 ч 5 мин"
