from __future__ import annotations

from pathlib import Path

import pytest

from kvnbox.core.paths import Paths
from kvnbox.system.bbr import Bbr
from kvnbox.system.fail2ban import (
    TRAP_MARK,
    Fail2ban,
    SecurityError,
    jail_config,
    with_trap_rules,
)
from kvnbox.system.runner import FakeRunner, Result
from kvnbox.system.ufw import Ufw

FIX = Path(__file__).parent.parent / "fixtures" / "system"
UFW_STATUS = (FIX / "ufw_status_numbered.txt").read_text(encoding="utf-8")


def ufw_fake() -> FakeRunner:
    return FakeRunner().on("ufw", "status", "numbered", result=Result(0, UFW_STATUS))


# ─── UFW ─────────────────────────────────────────────────────────────────────


def test_delete_by_comment_from_the_end() -> None:
    fake = ufw_fake()
    assert Ufw(fake).delete_by_comment("Mieru TCP", "Mieru UDP") == 4
    deletes = [c for c in fake.calls if c[:3] == ["ufw", "--force", "delete"]]
    assert [c[3] for c in deletes] == ["10", "9", "5", "4"]


def test_delete_port_and_is_allowed() -> None:
    fake = ufw_fake()
    ufw = Ufw(fake)
    assert ufw.is_allowed("443/udp") and not ufw.is_allowed("80/tcp")
    assert ufw.delete_port("443/tcp") == 2


def test_allow_with_comment() -> None:
    fake = FakeRunner()
    Ufw(fake).allow("21000/udp", "Mieru UDP")
    assert fake.calls == [["ufw", "allow", "21000/udp", "comment", "Mieru UDP"]]


# ─── ловушка и fail2ban ──────────────────────────────────────────────────────

BEFORE_RULES = "*filter\n:ufw-before-input - [0:0]\n-A ufw-before-input -j ACCEPT\nCOMMIT\n"


def test_trap_rules_insert_idempotent_and_remove() -> None:
    once = with_trap_rules(BEFORE_RULES, "ufw-before-input", True)
    twice = with_trap_rules(once, "ufw-before-input", True)
    assert once == twice
    assert once.count(TRAP_MARK) == 1
    lines = once.splitlines()
    assert lines.index("COMMIT") == len(lines) - 1  # правило перед COMMIT
    assert TRAP_MARK in lines[-2]
    assert with_trap_rules(once, "ufw-before-input", False) == BEFORE_RULES


def test_jail_config_matches_transferbox() -> None:
    text = jail_config(2222)
    assert "[sshd]\nenabled = true\nport = 2222\n" in text
    assert "[portscan-trap]" in text and "maxretry = 1" in text
    assert "banaction = nftables[type=allports, blocktype=drop]" in text


def fail2ban(paths: Paths, fake: FakeRunner, installed: bool = True) -> Fail2ban:
    return Fail2ban(fake, paths, which=lambda name: f"/usr/bin/{name}" if installed else None)


def test_enable_flow(paths: Paths) -> None:
    paths.ufw_before_rules.parent.mkdir(parents=True)
    paths.ufw_before_rules.write_text(BEFORE_RULES)
    fake = ufw_fake()
    fail2ban(paths, fake).enable(ssh_port=22)
    assert "port = 22" in paths.fail2ban_jail.read_text()
    assert "PORTSCAN-TRAP" in paths.fail2ban_filter.read_text()
    assert TRAP_MARK in paths.ufw_before_rules.read_text()
    assert not paths.ufw_before6_rules.exists()  # нет файла — не создаём
    assert fake.called("ufw", "reload")
    assert fake.calls[-2:] == [
        ["systemctl", "enable", "fail2ban"],
        ["systemctl", "restart", "fail2ban"],
    ]
    assert not fake.called("apt-get")


def test_enable_installs_when_missing(paths: Paths) -> None:
    fake = ufw_fake().on("apt-get", "install", result=Result(100, "", "E: no net"))
    with pytest.raises(SecurityError, match="no net"):
        fail2ban(paths, fake, installed=False).enable(22)


def test_status_inactive_skips_client(paths: Paths) -> None:
    fake = ufw_fake().on("systemctl", "is-active", result=Result(3))
    st = fail2ban(paths, fake).status()
    assert not st.active and st.jails == []
    assert not fake.called("fail2ban-client")


def test_status_active(paths: Paths) -> None:
    fake = (
        ufw_fake()
        .on(
            "fail2ban-client",
            "status",
            "sshd",
            result=Result(0, (FIX / "f2b_status_sshd.txt").read_text()),
        )
        .on(
            "fail2ban-client",
            "status",
            "portscan-trap",
            result=Result(0, (FIX / "f2b_status_trap.txt").read_text()),
        )
        .on("fail2ban-client", "status", result=Result(0, (FIX / "f2b_status.txt").read_text()))
    )
    st = fail2ban(paths, fake).status()
    assert st.active and [j.name for j in st.jails] == ["portscan-trap", "sshd"]
    assert len(st.banned_ips) >= 8 and st.banned_ips == sorted(st.banned_ips)


def test_unban_validates_ip(paths: Paths) -> None:
    fake = FakeRunner()
    f = fail2ban(paths, fake)
    with pytest.raises(SecurityError):
        f.unban("1.2.3.4; rm -rf /")
    f.unban(" 203.0.113.5 ")
    assert fake.calls == [["fail2ban-client", "unban", "203.0.113.5"]]


# ─── BBR ─────────────────────────────────────────────────────────────────────


def _proc(paths: Paths, cc: str, available: str = "reno cubic bbr") -> None:
    d = paths.proc / "sys" / "net" / "ipv4"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tcp_congestion_control").write_text(cc + "\n")
    (d / "tcp_available_congestion_control").write_text(available + "\n")


def test_bbr_state(paths: Paths) -> None:
    _proc(paths, "bbr")
    assert Bbr(FakeRunner(), paths).is_active()
    _proc(paths, "cubic")
    assert not Bbr(FakeRunner(), paths).is_active()


def _kernel(paths: Paths) -> FakeRunner:
    """sysctl -w меняет «ядро» (proc) по-настоящему."""

    def sysctl_w(argv: list[str], _: str | None) -> Result:
        key, value = argv[2].split("=")
        if key == "net.ipv4.tcp_congestion_control":
            _proc(paths, value)
        return Result(0)

    return FakeRunner().on("sysctl", "-w", result=sysctl_w)


def test_bbr_enable_disable(paths: Paths) -> None:
    _proc(paths, "cubic")
    fake = _kernel(paths)
    bbr = Bbr(fake, paths)
    assert bbr.enable() and bbr.is_active()
    assert "tcp_congestion_control=bbr" in paths.sysctl_bbr.read_text()
    assert ["sysctl", "-w", "net.ipv4.tcp_congestion_control=bbr"] in fake.calls
    assert bbr.disable() and not bbr.is_active()
    assert not paths.sysctl_bbr.exists()
    # cubic выставляется ПОСЛЕ перечитывания sysctl.d, иначе оно вернёт bbr
    assert fake.calls[-2:] == [
        ["sysctl", "--system"],
        ["sysctl", "-w", "net.ipv4.tcp_congestion_control=cubic"],
    ]


def test_bbr_disable_removes_transferbox_file(paths: Paths) -> None:
    """Стенд: файл TransferBox оставался и возвращал bbr через sysctl --system."""
    _proc(paths, "bbr")
    legacy = paths.etc / "sysctl.d" / "99-transferbox-bbr.conf"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n")
    bbr = Bbr(_kernel(paths), paths)
    assert bbr.disable() and not bbr.is_active()
    assert not legacy.exists()
    assert bbr.foreign_sources() == []


def test_bbr_disable_fails_when_kernel_keeps_bbr(paths: Paths) -> None:
    _proc(paths, "bbr")
    assert not Bbr(FakeRunner(), paths).disable()  # sysctl «успешен», но ядро не сменилось


def test_bbr_foreign_sources(paths: Paths) -> None:
    d = paths.etc / "sysctl.d"
    d.mkdir(parents=True, exist_ok=True)
    (d / "10-hoster.conf").write_text("net.ipv4.tcp_congestion_control = bbr\n")
    (d / "20-other.conf").write_text("# net.ipv4.tcp_congestion_control=bbr\nvm.swappiness=10\n")
    (paths.etc / "sysctl.conf").write_text("net/ipv4/tcp_congestion_control=bbr\n")
    assert Bbr(FakeRunner(), paths).foreign_sources() == [
        paths.etc / "sysctl.conf",
        d / "10-hoster.conf",
    ]


def test_bbr_unsupported(paths: Paths) -> None:
    _proc(paths, "cubic", available="reno cubic")
    fake = FakeRunner().on("modprobe", result=Result(1))
    assert not Bbr(fake, paths).enable()
    assert not paths.sysctl_bbr.exists()
