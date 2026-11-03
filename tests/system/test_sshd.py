from __future__ import annotations

from pathlib import Path

import pytest

from kvnbox.core.paths import Paths
from kvnbox.system.runner import FakeRunner, Responder, Result
from kvnbox.system.sshd import Sshd, SshError

FIX = Path(__file__).parent.parent / "fixtures" / "system"
KEY1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA1111111111111111111111111111111111111111 one"
KEY2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIB2222222222222222222222222222222222222222 two"


def sshd_T_from_files(paths: Paths) -> Responder:
    """`sshd -T`, который отражает текущий drop-in и sshd_config в tmp."""

    def respond(argv: list[str], _: str | None) -> Result:
        dropin = paths.ssh_auth_dropin.read_text() if paths.ssh_auth_dropin.exists() else ""
        mode = "no" if "PasswordAuthentication no" in dropin else "yes"
        port = "22"
        if paths.sshd_config.exists():
            for line in paths.sshd_config.read_text().splitlines():
                if line.startswith("Port "):
                    port = line.split()[1]
                    break
        return Result(0, f"port {port}\npasswordauthentication {mode}\n")

    return respond


def fake_for(paths: Paths, **overrides: Result) -> FakeRunner:
    fake = FakeRunner()
    for prefix, res in overrides.items():
        fake.on(*prefix.split("_"), result=res)
    fake.on("sshd", "-T", result=sshd_T_from_files(paths))
    fake.on(
        "ssh-keygen",
        "-lf",
        result=_fake_keygen,
    )
    fake.on("systemctl", "is-enabled", "--quiet", "ssh.socket", result=Result(1))
    fake.on("systemctl", "is-active", "--quiet", "ssh.socket", result=Result(3))
    fake.on(
        "ss",
        "-Htln",
        result=lambda argv, inp: Result(0, f"LISTEN 0 128 0.0.0.0:{_port(paths)} 0.0.0.0:*\n"),
    )
    return fake


def _fake_keygen(argv: list[str], inp: str | None) -> Result:
    """Как `ssh-keygen -lf -`: отпечаток только для строк вида «ssh-… AAAA…»."""
    parts = (inp or "").split()
    if len(parts) < 2 or not parts[0].startswith("ssh-") or not parts[1].startswith("AAAA"):
        return Result(255, "", "(stdin) is not a public key file.")
    return Result(0, f"256 SHA256:fp-{parts[-1]} x (ED25519)\n")


def _port(paths: Paths) -> str:
    for line in paths.sshd_config.read_text().splitlines():
        if line.startswith("Port "):
            return line.split()[1]
    return "22"


@pytest.fixture
def setup(paths: Paths) -> Paths:
    paths.sshd_config.parent.mkdir(parents=True)
    paths.sshd_config.write_text(
        "Include /etc/ssh/sshd_config.d/*.conf\n#Port 22\nPermitRootLogin yes\n"
    )
    return paths


def write_keys(paths: Paths, *keys: str) -> None:
    paths.authorized_keys.parent.mkdir(parents=True, exist_ok=True)
    paths.authorized_keys.write_text("".join(k + "\n" for k in keys))


def test_real_sshd_T_port_and_mode(paths: Paths) -> None:
    fake = FakeRunner().on("sshd", "-T", result=Result(0, (FIX / "sshd_T.txt").read_text()))
    s = Sshd(fake, paths)
    assert s.port() == 22 and s.auth_mode() == "password"


def test_fallback_without_sshd_T(setup: Paths) -> None:
    setup.sshd_config.write_text("Port 2222\n")
    fake = FakeRunner().on("sshd", "-T", result=Result(255, "", "no hostkeys"))
    assert Sshd(fake, setup).port() == 2222


def test_authorized_keys_parsing(setup: Paths) -> None:
    write_keys(setup, "# comment", KEY1, "", "garbage", KEY2)
    keys = Sshd(fake_for(setup), setup).authorized_keys()
    assert [(k.index, k.comment) for k in keys] == [(1, "one"), (2, "two")]
    assert keys[0].fingerprint == "SHA256:fp-one"


def test_add_key_validates_and_dedups(setup: Paths) -> None:
    s = Sshd(fake_for(setup), setup)
    s.add_key(KEY1)
    with pytest.raises(SshError, match="уже"):
        s.add_key(KEY1)
    with pytest.raises(SshError):
        s.add_key("not a key")
    assert setup.authorized_keys.read_text() == KEY1 + "\n"


def test_key_mode_requires_keys(setup: Paths) -> None:
    with pytest.raises(SshError, match="нет ни одного"):
        Sshd(fake_for(setup), setup).set_auth_mode("key")
    assert not setup.ssh_auth_dropin.exists()


def test_set_auth_mode_key(setup: Paths) -> None:
    write_keys(setup, KEY1)
    fake = fake_for(setup)
    s = Sshd(fake, setup)
    s.set_auth_mode("key")
    assert "PasswordAuthentication no" in setup.ssh_auth_dropin.read_text()
    assert s.auth_mode() == "key"
    assert ["sshd", "-t"] in fake.calls and ["systemctl", "restart", "ssh"] in fake.calls


def test_set_auth_mode_rolls_back_on_bad_config(setup: Paths) -> None:
    write_keys(setup, KEY1)
    fake = fake_for(setup, **{"sshd_-t": Result(255, "", "bad option")})
    with pytest.raises(SshError, match="bad option"):
        Sshd(fake, setup).set_auth_mode("key")
    assert not setup.ssh_auth_dropin.exists()


def test_restart_falls_back_to_sshd_unit(setup: Paths) -> None:
    fake = fake_for(setup).on("systemctl", "restart", "ssh", result=Result(5))
    # отдельный ответ для sshd добавлен позже — порядок: точный префикс ssh выше
    Sshd(fake, setup).restart()
    assert ["systemctl", "restart", "sshd"] in fake.calls


def test_socket_activation_restarts_socket(setup: Paths) -> None:
    fake = FakeRunner().on("systemctl", "is-enabled", "--quiet", "ssh.socket", result=Result(0))
    Sshd(fake, setup).restart()
    assert fake.calls[1:3] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", "ssh.socket"],
    ]


def test_delete_last_key_in_key_mode_enables_password(setup: Paths) -> None:
    write_keys(setup, KEY1)
    s = Sshd(fake_for(setup), setup)
    s.set_auth_mode("key")
    key, switched = s.delete_key(1)
    assert key.comment == "one" and switched
    assert s.auth_mode() == "password"
    assert setup.authorized_keys.read_text() == ""


def test_delete_key_keeps_others(setup: Paths) -> None:
    write_keys(setup, KEY1, KEY2)
    key, switched = Sshd(fake_for(setup), setup).delete_key(2)
    assert key.comment == "two" and not switched
    assert setup.authorized_keys.read_text() == KEY1 + "\n"
    with pytest.raises(SshError):
        Sshd(fake_for(setup), setup).delete_key(5)


def test_set_port(setup: Paths) -> None:
    s = Sshd(fake_for(setup), setup)
    s.set_port(2222)
    text = setup.sshd_config.read_text()
    assert "Port 2222\n" in text and "#Port 22" not in text
    assert s.port() == 2222


def test_set_port_rolls_back_if_not_listening(setup: Paths) -> None:
    before = setup.sshd_config.read_text()
    fake = fake_for(setup).on("ss", result=Result(0, "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"))
    fake.responses.insert(0, fake.responses.pop())  # этот ответ — приоритетнее
    with pytest.raises(SshError, match="не запустился"):
        Sshd(fake, setup).set_port(2222)
    assert setup.sshd_config.read_text() == before


def test_set_port_range(setup: Paths) -> None:
    with pytest.raises(SshError, match="Диапазон"):
        Sshd(fake_for(setup), setup).set_port(70000)


def test_ensure_include_added(setup: Paths) -> None:
    setup.sshd_config.write_text("Port 22\n")
    Sshd(fake_for(setup), setup).ensure_include()
    assert setup.sshd_config.read_text().startswith("Include /etc/ssh/sshd_config.d/*.conf\n")
