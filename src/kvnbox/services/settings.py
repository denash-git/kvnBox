"""Настройки панели: домен и email, FQDN, шаблон сайта, пути VLESS."""

from __future__ import annotations

import re
from collections.abc import Callable

from pydantic import ValidationError

from kvnbox.core.models import FAKE_SITE_TEMPLATES, Settings
from kvnbox.services import ServiceError
from kvnbox.services.app import App
from kvnbox.services.apply import Applier, ApplyResult


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    return str(err.get("msg", "некорректное значение")).removeprefix("Value error, ")


class SettingsService:
    def __init__(self, app: App, applier: Applier | None = None) -> None:
        self.app = app
        self.applier = applier or Applier(app)

    def get(self) -> Settings:
        return self.app.settings.load()

    def update(self, change: Callable[[Settings], None], apply: bool = True) -> ApplyResult:
        """Изменить настройки (с проверкой) и применить. При неудаче — откат."""
        before = self.app.settings.load()
        try:
            with self.app.settings.transaction() as s:
                change(s)
        except ValidationError as e:
            raise ServiceError(_first_error(e)) from e
        if not apply:
            return ApplyResult(True, "Сохранено.")
        result = self.applier.apply()
        if not result.ok:
            self.app.settings.save(before)
        return result

    def set_domain_email(self, domain: str, email: str) -> ApplyResult:
        def change(s: Settings) -> None:
            s.domain = domain
            s.admin_email = email

        return self.update(change)

    def set_site_template(self, template: str) -> ApplyResult:
        if template not in FAKE_SITE_TEMPLATES:
            raise ServiceError(f"Неизвестный шаблон: {template}")

        def change(s: Settings) -> None:
            s.site.template = template

        return self.update(change)

    def set_vless_paths(
        self,
        ws_path: str | None = None,
        grpc_service: str | None = None,
        httpupgrade_path: str | None = None,
    ) -> ApplyResult:
        def change(s: Settings) -> None:
            if ws_path is not None:
                s.vless.ws_path = ws_path
            if grpc_service is not None:
                s.vless.grpc_service = grpc_service
            if httpupgrade_path is not None:
                s.vless.httpupgrade_path = httpupgrade_path

        return self.update(change)

    def set_path(self, core: str, field: str, value: str) -> ApplyResult:
        """Путь одного ядра («vless» — sing-box, «xray»): ws_path, grpc_service,
        httpupgrade_path, xhttp_path. Совпадения путей ловит модель Settings."""
        if core not in ("vless", "xray") or field not in (
            "ws_path",
            "grpc_service",
            "httpupgrade_path",
            "xhttp_path",
        ):
            raise ServiceError(f"Неизвестная настройка пути: {core}.{field}")
        if core == "xray" and not value.strip("/"):
            raise ServiceError("У Xray путь и имя сервиса не могут быть пустыми.")

        def change(s: Settings) -> None:
            setattr(getattr(s, core), field, value)

        return self.update(change)

    def set_fqdn(self, fqdn: str) -> None:
        """Имя хоста VPS: hostnamectl + замена старого имени в /etc/hosts (как в TransferBox)."""
        fqdn = fqdn.replace(" ", "")
        if not re.fullmatch(r"[A-Za-z0-9.-]+", fqdn):
            raise ServiceError("Некорректное имя: латиница, цифры, «-» и точки.")
        old = self.app.runner.run(["hostname"], timeout=10).stdout.strip()
        res = self.app.runner.run(["hostnamectl", "set-hostname", fqdn], timeout=30)
        if not res.ok:
            raise ServiceError(f"Не удалось сменить имя хоста: {res.output}")
        if old:
            self._replace_in_hosts(old, fqdn)
        self.update(lambda s: setattr(s, "fqdn", fqdn), apply=False)

    def _replace_in_hosts(self, old: str, new: str) -> None:
        hosts = self.app.paths.etc / "hosts"
        try:
            text = hosts.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        # имя целиком: соседние символы не из имени хоста (в имени бывают «-» и «.»)
        pattern = rf"(?<![A-Za-z0-9.-]){re.escape(old)}(?![A-Za-z0-9.-])"
        hosts.write_text(re.sub(pattern, new, text), encoding="utf-8")

    def recreate(self) -> ApplyResult:
        """«Пересоздать конфигурации» — применить без изменений данных."""
        return self.applier.apply()
