"""Read-only проверки на настоящей системе (стенд). Ничего не меняют.

Запуск: KVNBOX_SYSTEM_TESTS=1 pytest -m system
"""

from __future__ import annotations

import os

import pytest

from kvnbox.core.paths import Paths
from kvnbox.system.bbr import Bbr
from kvnbox.system.fail2ban import Fail2ban
from kvnbox.system.metrics import Metrics, cpu_percent
from kvnbox.system.runner import SubprocessRunner
from kvnbox.system.sshd import Sshd
from kvnbox.system.systemd import Systemd
from kvnbox.system.ufw import Ufw

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        os.environ.get("KVNBOX_SYSTEM_TESTS") != "1",
        reason="только на стенде (KVNBOX_SYSTEM_TESTS=1)",
    ),
]

RUNNER = SubprocessRunner()
PATHS = Paths()


def test_services_state() -> None:
    sd = Systemd(RUNNER)
    for unit in ("caddy", "sing-box"):
        assert sd.is_active(unit), unit
    assert sd.state("kvnbox-no-such-unit") in ("inactive", "unknown")


def test_ufw_live() -> None:
    active, rules = Ufw(RUNNER).status()
    assert active and any(r.port_proto == "443/tcp" for r in rules)


def test_sshd_live() -> None:
    s = Sshd(RUNNER, PATHS)
    assert s.port() == 22
    assert s.auth_mode() in ("password", "key")
    assert s.is_listening(s.port())
    assert s.authorized_keys()  # стенд доступен по ключу


def test_fail2ban_live() -> None:
    st = Fail2ban(RUNNER, PATHS).status()
    assert st.active and {j.name for j in st.jails} >= {"sshd", "portscan-trap"}
    assert not st.port80_allowed


def test_bbr_and_metrics_live() -> None:
    Bbr(RUNNER, PATHS).is_active()  # просто читается
    m = Metrics(PATHS)
    a = m.cpu_times()
    assert 0 <= cpu_percent(a, m.cpu_times()) <= 100
    assert m.memory().total_mb > 500
    assert 0 < m.disk().percent < 100
    net = m.net()
    assert net is not None and net.rx_bytes > 0
    assert m.uptime_seconds() > 0
