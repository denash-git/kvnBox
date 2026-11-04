"""Фаервол UFW."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from kvnbox.system.runner import Result, Runner


@dataclass(frozen=True)
class UfwRule:
    num: int
    to: str  # «22/tcp», «443/udp (v6)»
    action: str  # ALLOW / DENY / REJECT / LIMIT
    source: str
    comment: str = ""

    @property
    def v6(self) -> bool:
        return self.to.endswith("(v6)")

    @property
    def port_proto(self) -> str:
        return self.to.removesuffix("(v6)").strip()


_RULE_RE = re.compile(
    r"^\[\s*(?P<num>\d+)\]\s+(?P<to>.+?)\s{2,}"
    r"(?P<action>ALLOW|DENY|REJECT|LIMIT)(?:\s+(?:IN|OUT|FWD))?\s+"
    r"(?P<source>.+?)\s*(?:#\s*(?P<comment>.*?))?\s*$"
)


def parse_status_numbered(text: str) -> tuple[bool, list[UfwRule]]:
    active = bool(re.search(r"^Status:\s*active\s*$", text, re.M))
    rules = []
    for line in text.splitlines():
        m = _RULE_RE.match(line.strip())
        if m:
            rules.append(
                UfwRule(
                    num=int(m["num"]),
                    to=m["to"].strip(),
                    action=m["action"],
                    source=m["source"].strip(),
                    comment=(m["comment"] or "").strip(),
                )
            )
    return active, rules


class Ufw:
    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    def _ufw(self, *args: str) -> Result:
        return self.runner.run(["ufw", *args], timeout=60)

    def status(self) -> tuple[bool, list[UfwRule]]:
        res = self._ufw("status", "numbered")
        return parse_status_numbered(res.stdout) if res.ok else (False, [])

    def rules(self) -> list[UfwRule]:
        return self.status()[1]

    def is_allowed(self, port_proto: str) -> bool:
        return any(r.action == "ALLOW" and r.port_proto == port_proto for r in self.rules())

    def allow(self, port_proto: str, comment: str | None = None) -> Result:
        return self._ufw("allow", port_proto, *(["comment", comment] if comment else []))

    def delete_allow(self, port_proto: str) -> Result:
        """`ufw delete allow X` — удаляет и v4, и v6 правило, созданные `ufw allow X`."""
        return self._ufw("delete", "allow", port_proto)

    def delete_where(self, predicate: Callable[[UfwRule], bool]) -> int:
        """Удалить все правила, подходящие под условие. Возвращает число удалённых."""
        targets = sorted((r.num for r in self.rules() if predicate(r)), reverse=True)
        for num in targets:  # с конца — номера оставшихся не сдвигаются
            self._ufw("--force", "delete", str(num))
        return len(targets)

    def delete_by_comment(self, *comments: str) -> int:
        return self.delete_where(lambda r: r.comment in comments)

    def delete_port(self, port_proto: str) -> int:
        """Удалить все ALLOW-правила для порта (v4 и v6), как бы они ни создавались."""
        return self.delete_where(lambda r: r.action == "ALLOW" and r.port_proto == port_proto)

    def reload(self) -> Result:
        return self._ufw("reload")

    def reset(self) -> Result:
        """Все правила UFW — в исходное состояние, фаервол выключен."""
        return self._ufw("--force", "reset")

    def default(self, policy: str, direction: str) -> Result:
        return self._ufw("default", policy, direction)

    def enable(self) -> Result:
        return self._ufw("--force", "enable")
