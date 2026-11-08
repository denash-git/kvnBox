# 📦 kvnBox

[![OS](https://img.shields.io/badge/OS-Debian%2013-blue.svg)](https://debian.org)
[![Release](https://img.shields.io/github/v/tag/denash-git/kvnBox?label=version&color=green)](https://github.com/denash-git/kvnBox/tags)

**kvnBox** — панель для личного VPN/прокси-сервера: 5 современных протоколов на одном сервере,
управление из меню в терминале и через Telegram-бот.

---

## ✨ Возможности

- **Протоколы:** VLESS (WebSocket, gRPC, HTTPUpgrade, XHTTP), NaiveProxy, Mieru, AmneziaWG 3.1, NetBird
- **Маскировка:** под сайт, выбор шаблона
- **Пользователи:** ссылки, QR-коды и подписка для каждого; включение и отключение в один клик
- **Telegram-бот**
- **Диагностика:** журналы, ресурсы сервера, тест скорости
- **Бэкап и восстановление**, в том числе перенос на другой сервер
- **Безопасность:** вход по ключу SSH, защита от сканеров Fail2ban
- **Обновление** панели и компонентов прямо из меню

---

## 💻 Требования

- Чистый **Debian 13** (amd64 или arm64), доступ root, от 1 ГБ RAM
- **Домен**, A-запись которого указывает на IP сервера (до установки)
- Свободные порты **80** и **443**

---

## 🚀 Установка

**1. На сервере**, от root:

```bash
bash <(wget -qO- https://raw.githubusercontent.com/denash-git/kvnBox/main/install.sh)
```

Установщик спросит только домен. Меню управления — команда `menu`.

**2. На компьютере с Windows** (по желанию) — приложение kvnBox для входа в меню серверов. В PowerShell:

```powershell
irm https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1 | iex
```

Появится ярлык **«kvnBox»** на рабочем столе и в меню Пуск. В приложении — список ваших серверов:
вход в меню сервера одним нажатием, без пароля. При добавлении сервера нужны только IP, порт SSH
и один раз пароль root — ключ SSH приложение создаст и пропишет само; там же можно отключить
вход по паролю. Новые версии приложение предлагает установить само.

---

## 📱 Клиенты

- **[Karing](https://github.com/KaringX/karing)** — Windows, macOS, Linux, Android, iOS
- **[LxBox](https://github.com/Leadaxe/LxBox)** — Android

---

## ❤️ Благодарности

[Caddy](https://github.com/caddyserver/caddy) ·
[sing-box](https://github.com/SagerNet/sing-box) ·
[Xray](https://github.com/XTLS/Xray-core) ·
[NaiveProxy](https://github.com/klzgrad/naiveproxy) ·
[Mieru](https://github.com/enfein/mieru) ·
[AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) ·
[NetBird](https://github.com/netbirdio/netbird)
