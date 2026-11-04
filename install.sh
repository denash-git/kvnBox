#!/usr/bin/env bash
# =============================================================================
# kvnBox — установщик панели управления прокси-сервером (Debian 13)
#
#   bash <(wget -qO- https://raw.githubusercontent.com/denash-git/kvnBox/main/install.sh)
#
# Подробный вывод всех команд — в /var/log/kvnbox-install.log, на экране —
# только этапы: «-» ждёт, спиннер — выполняется, «✓» — готово (D-037).
# Версия панели: последняя из git-тегов vX.Y.Z (или KVNBOX_VERSION=X.Y.Z).
# Повторный запуск на установленной панели — восстановление: данные не трогаются.
# =============================================================================
set -o pipefail

REPO="denash-git/kvnBox"
ROOT="/opt/kvnbox"
VENV="${ROOT}/.venv"
DATA="${ROOT}/data"
CADDY_BIN="/usr/local/bin/caddy"
LOG="/var/log/kvnbox-install.log"
WORK=""          # временный каталог установки (удаляется при выходе)
SWAPFILE=""      # временный swap для сборки (удаляется при выходе)

# ── цвета ────────────────────────────────────────────────────────────────────
BLUE=$'\033[1;34m'; GREEN=$'\033[1;32m'; RED=$'\033[1;31m'; YELLOW=$'\033[1;33m'
CYAN=$'\033[1;36m'; WHITE=$'\033[97m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

TTY=false
[[ -t 1 ]] && TTY=true

die() { echo -e "\n${RED}✗ ОШИБКА: ${1}${RESET}" >&2; exit 1; }

# ─── экран этапов ────────────────────────────────────────────────────────────
STEPS=()          # «функция|Название»
LINES_TOTAL=0     # строк в списке на экране
SPIN_PID=""

add_step() { STEPS+=("$1|$2"); }

# Перерисовать строку списка с номером $1 (с 0) — курсор остаётся под списком.
draw_line() {
    local text=$2 up=$((LINES_TOTAL - $1))
    printf '\033[%dA\r\033[2K%s\033[%dB\r' "$up" "$text" "$up"
}

line_pending() { echo "  ${DIM}- $1${RESET}"; }
line_done()    { echo "  ${GREEN}✓${RESET} ${WHITE}$1${RESET}${2:+ ${DIM}$2${RESET}}"; }
line_warn()    { echo "  ${YELLOW}!${RESET} ${WHITE}$1${RESET}${2:+ ${YELLOW}$2${RESET}}"; }
line_fail()    { echo "  ${RED}✗ $1${RESET}"; }

fmt_time() { printf '%d:%02d' $(($1 / 60)) $(($1 % 60)); }

spinner() {  # $1 — номер строки, $2 — название
    # выход по TERM — с кодом 0: bash не печатает «Terminated», который сдвинул бы список
    trap - EXIT INT
    trap 'exit 0' TERM
    local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏) i=0 start=$EPOCHSECONDS
    while :; do
        draw_line "$1" "  ${CYAN}${frames[i++ % 10]}${RESET} ${WHITE}$2${RESET}  ${DIM}$(fmt_time $((EPOCHSECONDS - start)))${RESET}"
        sleep 0.1
    done
}

stop_spinner() {
    if [[ -n "$SPIN_PID" ]]; then
        kill "$SPIN_PID" 2>/dev/null
        wait "$SPIN_PID" 2>/dev/null
        SPIN_PID=""
    fi
}

show_list() {
    LINES_TOTAL=${#STEPS[@]}
    if $TTY; then
        printf '\033[?25l'  # спрятать курсор на время установки
        local s
        for s in "${STEPS[@]}"; do line_pending "${s#*|}"; done
    fi
}

NOTE=""   # пояснение к результату этапа («уже установлен», «пропущено»…)

# Этап: функция пишет только в лог. Код 0 — готово, 2 — предупреждение
# (установка идёт дальше), иное — ошибка (стоп, хвост лога на экран).
run_steps() {
    local i rc fn name started
    # служебные сообщения bash (например, «wait_for: No record of process» при
    # гонке со спиннером) — в журнал: любая лишняя строка сдвинула бы список
    exec 3>&2 2>>"$LOG"
    for i in "${!STEPS[@]}"; do
        fn="${STEPS[$i]%%|*}"; name="${STEPS[$i]#*|}"
        NOTE=""
        started=$EPOCHSECONDS
        echo -e "\n===== [$((i + 1))/${#STEPS[@]}] ${name} — $(date '+%F %T')" >>"$LOG"
        if $TTY; then
            spinner "$i" "$name" 2>/dev/null &
            SPIN_PID=$!
        else
            echo "[..] ${name}"
        fi
        "$fn" >>"$LOG" 2>&1 </dev/null
        rc=$?
        stop_spinner
        echo "===== rc=${rc}, $((EPOCHSECONDS - started)) с" >>"$LOG"
        if [[ $rc -eq 0 ]]; then
            $TTY && draw_line "$i" "$(line_done "$name" "$NOTE")" || echo "[ok] ${name} ${NOTE}"
        elif [[ $rc -eq 2 ]]; then
            $TTY && draw_line "$i" "$(line_warn "$name" "$NOTE")" || echo "[!!] ${name} ${NOTE}"
        else
            $TTY && draw_line "$i" "$(line_fail "$name")" || echo "[FAIL] ${name}"
            fail_report "$name"
        fi
    done
    exec 2>&3 3>&-
}

fail_report() {
    exec 2>&3
    $TTY && printf '\033[?25h'
    echo
    echo -e "${RED}✗ Установка остановлена на этапе «$1».${RESET}"
    [[ -n "$NOTE" ]] && echo -e "  ${NOTE}"
    echo -e "${DIM}──── последние строки журнала ────${RESET}"
    tail -n 20 "$LOG" | sed 's/^/  /'
    echo -e "${DIM}──────────────────────────────────${RESET}"
    echo -e "  Полный журнал: ${BOLD}${LOG}${RESET}"
    echo -e "  Исправьте причину и запустите установщик ещё раз — готовые этапы пройдут быстро."
    exit 1
}

# Обработчики — только для основного процесса. Фоновый спиннер — подпроцесс:
# если его остановить сразу после запуска (быстрый этап), сигнал может прийти
# раньше, чем bash сбросит в нём унаследованные ловушки, и тогда спиннер
# выполнил бы уборку основного процесса (удалил бы $WORK посреди установки).
MAIN_PID=$$
cleanup() {
    [[ $BASHPID == "$MAIN_PID" ]] || return 0
    stop_spinner
    if [[ -n "$SWAPFILE" && -f "$SWAPFILE" ]]; then
        swapoff "$SWAPFILE" 2>/dev/null
        rm -f "$SWAPFILE"
    fi
    [[ -n "$WORK" && -d "$WORK" ]] && rm -rf "$WORK"
    $TTY && printf '\033[?25h'
}
on_signal() {
    [[ $BASHPID == "$MAIN_PID" ]] || exit 0
    echo
    echo -e "${RED}Установка прервана.${RESET}"
    exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

# ─── вспомогательное ─────────────────────────────────────────────────────────
log() { echo "[kvnbox] $*"; }

fetch() {  # url → файл, с повторами
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 -o "$2" "$1"
}

check_sha256() {  # файл, ожидаемый хэш
    local got
    got=$(sha256sum "$1" | cut -d' ' -f1)
    log "sha256 $1: $got (ожидается $2)"
    [[ "$got" == "$2" ]] || { NOTE="Контрольная сумма не совпала: $(basename "$1")"; return 1; }
}

deb_version() { dpkg-query -W -f='${Version}' "$1" 2>/dev/null; }

kvn() { "${VENV}/bin/python" -m kvnbox "$@"; }

# ─── этапы ───────────────────────────────────────────────────────────────────
step_system() {
    log "ОС: ${PRETTY_NAME:-?}, архитектура: ${ARCH}, RAM: ${MEM_MB} MB, swap: ${SWAP_MB} MB"
    local free_mb
    free_mb=$(df -Pm / | awk 'NR==2 {print $4}')
    log "свободно на /: ${free_mb} MB"
    if ((free_mb < 1500)); then
        NOTE="Мало места на диске: ${free_mb} MB (нужно не меньше 1500 MB)."
        return 1
    fi
    WORK=$(mktemp -d /root/kvnbox-install.XXXXXX) || return 1
    chmod 700 "$WORK"
}

step_apt_update() {
    apt-get update -q
}

step_packages() {
    apt-get install -y -q --no-install-recommends \
        ca-certificates curl tar gzip gnupg cron sudo \
        python3 python3-venv \
        ufw openssl qrencode zip unzip libcap2-bin iperf3 \
        fail2ban ipset python3-systemd
}

step_source() {
    local ref url
    if [[ -n "$LOCAL_SRC" ]]; then
        log "исходники из ${LOCAL_SRC}"
        mkdir -p "$WORK/src-tree" || return 1
        tar -C "$LOCAL_SRC" --exclude=.git --exclude=.venv --exclude=__pycache__ -cf - . \
            | tar -C "$WORK/src-tree" -xf - || return 1
    else
        ref="${KVNBOX_VERSION:-}"
        if [[ -z "$ref" ]]; then
            ref=$(curl -fsSL --retry 3 "https://api.github.com/repos/${REPO}/tags?per_page=100" | python3 -c '
import json, re, sys
tags = [t["name"] for t in json.load(sys.stdin) if re.fullmatch(r"v\d+\.\d+\.\d+", t["name"])]
print(max(tags, key=lambda t: tuple(map(int, t[1:].split(".")))) if tags else "")
') || return 1
            ref="${ref#v}"
        fi
        if [[ -n "$ref" ]]; then
            url="https://github.com/${REPO}/archive/refs/tags/v${ref}.tar.gz"
        else
            log "тегов версий ещё нет — берётся ветка main"
            url="https://github.com/${REPO}/archive/refs/heads/main.tar.gz"
        fi
        log "скачивание ${url}"
        fetch "$url" "$WORK/src.tar.gz" || { NOTE="Не удалось скачать kvnBox с GitHub."; return 1; }
        mkdir -p "$WORK/x" && tar -xzf "$WORK/src.tar.gz" -C "$WORK/x" || return 1
        mv "$WORK"/x/* "$WORK/src-tree" || return 1
    fi
    [[ -f "$WORK/src-tree/src/kvnbox/__init__.py" && -f "$WORK/src-tree/versions.env" ]] \
        || { NOTE="В архиве нет файлов kvnBox."; return 1; }
    # shellcheck disable=SC1091
    source "$WORK/src-tree/versions.env" || return 1
    KVNBOX_VER=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$WORK/src-tree/src/kvnbox/__init__.py")
    [[ -n "$KVNBOX_VER" ]] || return 1
    NOTE="v${KVNBOX_VER}"
}

step_speedtest() {
    if command -v speedtest >/dev/null; then NOTE="уже установлен"; return 0; fi
    local keyring=/etc/apt/keyrings/ookla_speedtest-cli-archive-keyring.gpg
    mkdir -p /etc/apt/keyrings
    if fetch https://packagecloud.io/ookla/speedtest-cli/gpgkey "$WORK/ookla.key" \
        && gpg --batch --yes --dearmor -o "$keyring" "$WORK/ookla.key" \
        && echo "deb [signed-by=${keyring}] https://packagecloud.io/ookla/speedtest-cli/debian/ ${VERSION_CODENAME:-trixie} main" \
            >/etc/apt/sources.list.d/ookla_speedtest-cli.list \
        && apt-get update -q && apt-get install -y -q speedtest; then
        return 0
    fi
    rm -f /etc/apt/sources.list.d/ookla_speedtest-cli.list
    NOTE="не установлен (не критично: поставится из меню при первом тесте скорости)"
    return 2
}

step_bbr() {
    if [[ "$ENABLE_BBR" != "true" ]]; then NOTE="пропущено"; return 0; fi
    if ! grep -qw bbr /proc/sys/net/ipv4/tcp_available_congestion_control && ! modprobe tcp_bbr; then
        NOTE="ядро не поддерживает BBR"
        return 2
    fi
    printf 'net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\n' >/etc/sysctl.d/99-kvnbox-bbr.conf
    sysctl -w net.core.default_qdisc=fq && sysctl -w net.ipv4.tcp_congestion_control=bbr
}

install_deb() {  # пакет, версия, url, sha256
    if [[ "$(deb_version "$1")" == "$2" ]]; then NOTE="$2, уже установлен"; return 0; fi
    local deb="$WORK/$1.deb"
    log "скачивание $3"
    fetch "$3" "$deb" || { NOTE="Не удалось скачать $1 $2."; return 1; }
    check_sha256 "$deb" "$4" || return 1
    dpkg -i "$deb" || return 1
    NOTE="$2"
}

step_singbox() {
    local sha="SINGBOX_SHA256_${ARCH^^}"
    install_deb sing-box "$SINGBOX_VERSION" \
        "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box_${SINGBOX_VERSION}_linux_${ARCH}.deb" \
        "${!sha}"
}

step_mita() {
    local sha="MITA_SHA256_${ARCH^^}" had=false
    [[ -n "$(deb_version mita)" ]] && had=true
    install_deb mita "$MITA_VERSION" \
        "https://github.com/enfein/mieru/releases/download/v${MITA_VERSION}/mita_${MITA_VERSION}_${ARCH}.deb" \
        "${!sha}" || return 1
    if ! $had; then
        # как TransferBox: сервер Mieru установлен, но выключен до включения в меню
        systemctl disable --now mita || true
        NOTE="${NOTE}, выключен до включения в меню"
    fi
}

has_naive() {  # бинарник Caddy с модулем forward_proxy (NaiveProxy)
    # не «| grep -q»: grep закрывает канал раньше, caddy получает SIGPIPE,
    # и с pipefail проверка случайно ложна
    local modules
    modules=$("$1" list-modules 2>/dev/null) || return 1
    grep -qx 'http.handlers.forward_proxy' <<<"$modules"
}

caddy_ready() {
    [[ -x "$CADDY_BIN" ]] && has_naive "$CADDY_BIN"
}

step_swap() {
    if caddy_ready; then NOTE="не нужен"; return 0; fi
    if ((MEM_MB + SWAP_MB >= 2000)); then NOTE="не нужен (${MEM_MB} MB RAM)"; return 0; fi
    SWAPFILE=/root/kvnbox-build.swap
    { fallocate -l 2G "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count=2048; } || return 1
    chmod 600 "$SWAPFILE" && mkswap "$SWAPFILE" && swapon "$SWAPFILE" || return 1
    NOTE="2 GB на время сборки"
}

step_go() {
    if caddy_ready; then NOTE="не нужен"; return 0; fi
    local sha="GO_SHA256_${ARCH^^}" tgz="$WORK/go.tar.gz"
    fetch "https://go.dev/dl/go${GO_VERSION}.linux-${ARCH}.tar.gz" "$tgz" \
        || { NOTE="Не удалось скачать Go ${GO_VERSION}."; return 1; }
    check_sha256 "$tgz" "${!sha}" || return 1
    tar -xzf "$tgz" -C "$WORK" && rm -f "$tgz"
    NOTE="${GO_VERSION}, временно"
}

step_caddy() {
    if caddy_ready; then NOTE="уже установлен, с NaiveProxy"; return 0; fi
    # Подоболочка: окружение сборки не уходит в следующие этапы. HOME — временный:
    # Go пишет настройки и телеметрию в ~/.config/go, после установки следов нет.
    (
        export HOME="$WORK/home" GOTELEMETRY=off
        export GOROOT="$WORK/go" GOPATH="$WORK/gopath" GOBIN="$WORK/bin"
        export GOCACHE="$WORK/gocache" GOTMPDIR="$WORK/gotmp" GOFLAGS=-modcacherw CGO_ENABLED=0
        export PATH="$GOBIN:$GOROOT/bin:$PATH"
        mkdir -p "$HOME" "$GOPATH" "$GOBIN" "$GOCACHE" "$GOTMPDIR"
        go telemetry off 2>/dev/null
        go install "github.com/caddyserver/xcaddy/cmd/xcaddy@${XCADDY_VERSION}" || exit 1
        xcaddy build "$CADDY_VERSION" --output "$WORK/caddy" \
            --with "github.com/caddyserver/forwardproxy=${FORWARDPROXY_MODULE}" || exit 1
    ) || return 1
    has_naive "$WORK/caddy" || return 1
    install -m 755 "$WORK/caddy" "${CADDY_BIN}.new" && mv -f "${CADDY_BIN}.new" "$CADDY_BIN" || return 1
    setcap cap_net_bind_service=+ep "$CADDY_BIN" || return 1
    NOTE="${CADDY_VERSION}"
}

step_cleanup() {
    local w="${WORK:?}"
    rm -rf "${w:?}/go" "${w:?}/gopath" "${w:?}/bin" "${w:?}/gocache" "${w:?}/gotmp" "${w:?}/caddy" "${w:?}/home"
    if [[ -n "$SWAPFILE" ]]; then
        swapoff "$SWAPFILE" && rm -f "$SWAPFILE" && SWAPFILE=""
    fi
    return 0
}

step_files() {
    local rel="${ROOT}/releases/${KVNBOX_VER}" prev d
    mkdir -p "${ROOT}/releases" "$DATA" "${ROOT}/backups" || return 1
    chmod 755 "$ROOT" && chmod 700 "$DATA" "${ROOT}/backups"
    prev=$(readlink "${ROOT}/app" 2>/dev/null)
    rm -rf "$rel" && cp -a "$WORK/src-tree" "$rel" || return 1
    rm -rf "$rel/tests"   # тесты на сервере не нужны
    chown -R root:root "$rel" && chmod -R go-w "$rel"
    ln -sfn "$rel" "${ROOT}/.app.new" && mv -Tf "${ROOT}/.app.new" "${ROOT}/app" || return 1
    # хранятся текущая и предыдущая версии (как при обновлении из меню)
    for d in "${ROOT}"/releases/*/; do
        d="${d%/}"
        [[ "$d" == "$rel" || "$d" == "$prev" ]] || rm -rf "$d"
    done
    NOTE="${ROOT}"
}

step_venv() {
    if [[ -L "$VENV" || ! -x "$VENV/bin/python" ]]; then
        rm -rf "$VENV"
        python3 -m venv "$VENV" || return 1
    fi
    "$VENV/bin/pip" install -q --disable-pip-version-check --no-cache-dir --require-hashes \
        -r "${ROOT}/app/requirements.lock" || return 1
    local site
    site=$("$VENV/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') || return 1
    echo "${ROOT}/app/src" >"$site/kvnbox.pth"
    "$VENV/bin/python" -c 'import kvnbox, aiogram, pydantic; print("kvnbox", kvnbox.__version__)' || return 1
    NOTE="Python $("$VENV/bin/python" -c 'import platform; print(platform.python_version())')"
}

step_commands() {
    local name entry
    for name in kvnbox menu; do
        entry=main; [[ $name == menu ]] && entry=menu_main
        cat >"$VENV/bin/$name" <<EOF
#!${VENV}/bin/python
import sys

from kvnbox.cli.main import ${entry}

sys.exit(${entry}())
EOF
        chmod 755 "$VENV/bin/$name"
        ln -sfn "$VENV/bin/$name" "/usr/local/bin/$name"
    done
}

step_services() {
    # файл службы caddy пишет сама панель (он же обновляется при `kvnbox apply`)
    "$VENV/bin/python" -c '
from kvnbox.services.app import App
from kvnbox.system.caddy_unit import ensure_caddy_unit
app = App.create()
ensure_caddy_unit(app.paths, app.systemd)
' || return 1
    systemctl daemon-reload && systemctl enable caddy sing-box
}

step_init() {
    if [[ -f "$DATA/settings.json" ]]; then NOTE="данные уже есть — сохранены"; return 0; fi
    local args=(init --domain "$DOMAIN")
    [[ -n "$EMAIL" ]] && args+=(--email "$EMAIL")
    [[ "$ENABLE_BBR" != "true" ]] && args+=(--no-bbr)
    kvn "${args[@]}"
}

step_firewall() { kvn firewall setup; }

step_apply() {
    kvn apply || { NOTE="Конфигурация не применилась — подробности ниже."; return 1; }
}

step_security() {
    # восстановление/переезд: не включать защиту, которую владелец выключил
    if $REPAIR && ! "$VENV/bin/python" -c '
import sys
from kvnbox.services.app import App
sys.exit(0 if App.create().settings.load().security.scanner_protection else 1)
'; then
        NOTE="выключена в настройках"
        return 0
    fi
    kvn security enable && return 0
    NOTE="не включилась (можно включить в меню: Настройки → Защита)"
    return 2
}

step_check() {
    local unit i bad=""
    for unit in caddy sing-box; do
        for i in $(seq 1 15); do
            systemctl is-active --quiet "$unit" && break
            sleep 1
        done
        systemctl is-active --quiet "$unit" || bad+=" $unit"
    done
    systemctl --no-pager status caddy sing-box | head -n 30
    [[ -z "$bad" ]] || { NOTE="Не запустились:${bad}"; return 1; }
}

# ─── начало ──────────────────────────────────────────────────────────────────
[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "Запустите установщик от root: sudo bash install.sh"
[[ -f /etc/os-release ]] || die "Не удалось определить операционную систему."
# shellcheck disable=SC1091
source /etc/os-release
command -v apt-get >/dev/null || die "Нужен Debian (apt-get не найден)."
((BASH_VERSINFO[0] >= 5)) || die "Нужен bash 5 или новее."

case "$(dpkg --print-architecture 2>/dev/null || uname -m)" in
    amd64 | x86_64) ARCH=amd64 ;;
    arm64 | aarch64) ARCH=arm64 ;;
    *) die "Неподдерживаемая архитектура: $(uname -m)" ;;
esac
MEM_MB=$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)
SWAP_MB=$(awk '/^SwapTotal:/ {print int($2 / 1024)}' /proc/meminfo)
export DEBIAN_FRONTEND=noninteractive

# Запуск из папки с исходниками (git clone) — без скачивания
LOCAL_SRC=""
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
if [[ -n "$SELF_DIR" && -f "$SELF_DIR/src/kvnbox/__init__.py" && -f "$SELF_DIR/versions.env" ]]; then
    LOCAL_SRC="$SELF_DIR"
fi

$TTY && [[ -n "${TERM:-}" ]] && clear
echo -e "${BLUE}════════════════════════════════════════════════════════════${RESET}"
echo -e "${YELLOW}                          kvnBox                            ${RESET}"
echo -e "${YELLOW}               Установка Панели Управления                  ${RESET}"
echo -e "${BLUE}────────────────────────────────────────────────────────────${RESET}"
echo -e "  Добро пожаловать в установщик kvnBox!"
echo -e "  Скрипт настроит все в автоматическом режиме."
echo -e "${BLUE}════════════════════════════════════════════════════════════${RESET}"
echo

DOMAIN="" EMAIL="" ENABLE_BBR="true"
if [[ -f "$DATA/settings.json" ]]; then
    REPAIR=true
    echo -e "  ${CYAN}Панель уже установлена — режим восстановления.${RESET}"
    echo -e "  ${DIM}Пользователи и настройки сохраняются, компоненты проверяются и доустанавливаются.${RESET}"
    DOMAIN=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["domain"])' "$DATA/settings.json" 2>/dev/null || echo "?")
else
    REPAIR=false
    [[ -r /dev/tty ]] || die "Нужен интерактивный терминал для ввода настроек."
    echo -e "${BLUE}[*] Сбор настроек установки${RESET}"
    while [[ -z "$DOMAIN" ]]; do
        read -rp "  Введите ваш домен (например, proxy.example.com): " DOMAIN </dev/tty
        DOMAIN="${DOMAIN// /}"
        if [[ -n "$DOMAIN" && ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]]; then
            echo -e "  ${RED}Неверный домен.${RESET}"
            DOMAIN=""
        fi
    done
    read -rp "  Введите email для Let's Encrypt (по умолчанию пусто — анонимный SSL): " EMAIL </dev/tty
    EMAIL="${EMAIL// /}"
    read -rp "  Включить BBR (TCP оптимизация)? [Y/n]: " bbr_input </dev/tty
    [[ "${bbr_input,,}" == "n" ]] && ENABLE_BBR="false"
fi
echo

umask 077
: >"$LOG" && chmod 600 "$LOG"
echo "kvnBox install — $(date '+%F %T'), repair=${REPAIR}, arch=${ARCH}" >>"$LOG"
umask 022

add_step step_system      "Проверка системы"
add_step step_apt_update  "Обновление списка пакетов"
add_step step_packages    "Установка системных пакетов"
add_step step_source      "Загрузка kvnBox"
add_step step_speedtest   "Ookla Speedtest"
$REPAIR || add_step step_bbr "TCP BBR"
add_step step_singbox     "sing-box"
add_step step_mita        "Mieru (mita)"
add_step step_swap        "Временный файл подкачки"
add_step step_go          "Загрузка Go"
add_step step_caddy       "Сборка Caddy + NaiveProxy (до 5 мин)"
add_step step_cleanup     "Очистка временных файлов"
add_step step_files       "Файлы панели"
add_step step_venv        "Python-окружение"
add_step step_commands    "Команды menu и kvnbox"
add_step step_services    "Службы systemd"
add_step step_init        "Первичная настройка"
add_step step_firewall    "Фаервол UFW"
add_step step_apply       "Запуск прокси-серверов"
add_step step_security    "Защита от сканеров"
add_step step_check       "Проверка служб"

START=$EPOCHSECONDS
show_list
run_steps
$TTY && printf '\033[?25h'

echo
echo -e "${GREEN}✓ Установка завершена за $(fmt_time $((EPOCHSECONDS - START))).${RESET}"
echo -e "  Версия: ${GREEN}kvnBox ${KVNBOX_VER}${RESET}"
echo -e "  Домен: ${GREEN}${DOMAIN}${RESET}"
echo -e "  Команда управления: ${GREEN}menu${RESET}"
echo
if ! $REPAIR; then
    echo -e "  Сгенерированы стартовые учетные записи для пользователя ${BOLD}admin${RESET}."
    echo -e "  Запустите ${GREEN}menu${RESET} -> выберите ${BOLD}1${RESET}, чтобы просмотреть ссылки подключения и QR-коды."
    echo
    echo -e "  ${YELLOW}⚠️ Важное примечание по SSL:${RESET}"
    echo -e "  Если ваш домен ${BOLD}${DOMAIN}${RESET} еще не направлен на IP этого сервера,"
    echo -e "  сертификат Let's Encrypt не будет получен сразу. Caddy автоматически"
    echo -e "  получит SSL-сертификат, как только обновятся DNS-записи домена."
    echo
fi
echo -e "  ${DIM}Журнал установки: ${LOG}${RESET}"
echo
