# kvnBox

Панель управления прокси-сервером: Caddy + NaiveProxy, sing-box (VLESS), Mieru.

## Установка

Debian 13 (amd64 / arm64), от root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/denash-git/kvnBox/main/install.sh)
```

Установщик спросит домен, email для Let's Encrypt (можно пусто) и включать ли BBR.
Подробный журнал — `/var/log/kvnbox-install.log`.

Управление — команда `menu`.

Повторный запуск установщика на установленной панели — восстановление:
пользователи и настройки сохраняются.
