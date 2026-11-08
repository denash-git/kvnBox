# kvnBox: вход в меню сервера с компьютера Windows одним ярлыком.
# Запуск (PowerShell), после установки kvnBox на сервер:
#   irm https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1 | iex
# Спрашивает IP и порт, один раз пароль root. Сам создаёт ключ SSH, добавляет его на
# сервер, прописывает сервер в ~/.ssh/config и кладёт ярлык на рабочий стол.
# Повторный запуск — другой сервер или новый порт SSH: ярлык и запись обновятся.

$ErrorActionPreference = 'Stop'

function Get-KvnboxTool([string]$Name) {
    $cmd = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $path = Join-Path $env:SystemRoot "System32\OpenSSH\$Name.exe"
    if (Test-Path $path) { return $path }
    return $null
}

function Read-KvnboxValue([string]$Prompt, [string]$Default = '') {
    $text = if ($Default) { "$Prompt [$Default]" } else { $Prompt }
    $value = (Read-Host $text).Trim()
    if ($value) { return $value }
    return $Default
}

function Get-KvnboxAlias([string]$Server) {
    return 'kvnbox-' + (($Server.ToLower()) -replace '[^a-z0-9.-]', '-')
}

function New-KvnboxKey([string]$KeyPath) {
    # ключ без парольной фразы: ярлык открывает меню без вопросов
    if (Test-Path $KeyPath) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path $KeyPath) | Out-Null
    $keygen = Get-KvnboxTool 'ssh-keygen'
    $argLine = "-q -t ed25519 -N `"`" -C `"kvnbox-$env:COMPUTERNAME`" -f `"$KeyPath`""
    $p = Start-Process -FilePath $keygen -ArgumentList $argLine -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0 -or -not (Test-Path $KeyPath)) { throw 'Не удалось создать ключ SSH.' }
}

function Set-KvnboxSshConfig {
    param([string]$ConfigPath, [string]$Alias, [string]$Server, [int]$Port, [string]$KeyPath)
    $block = @(
        "Host $Alias",
        "    HostName $Server",
        "    Port $Port",
        "    User root",
        "    IdentityFile `"$KeyPath`"",
        "    IdentitiesOnly yes",
        "    ServerAliveInterval 30"
    )
    $lines = @()
    if (Test-Path $ConfigPath) { $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8) }
    # прежняя запись этого сервера убирается целиком (до следующего Host/Match)
    $out = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*(Host|Match)\s') { $skip = ($line.Trim() -eq "Host $Alias") }
        if (-not $skip) { $out.Add($line) }
    }
    while ($out.Count -gt 0 -and -not $out[$out.Count - 1].Trim()) { $out.RemoveAt($out.Count - 1) }
    if ($out.Count -gt 0) { $out.Add('') }
    $out.AddRange([string[]]$block)
    New-Item -ItemType Directory -Force -Path (Split-Path $ConfigPath) | Out-Null
    # без BOM: OpenSSH не читает файл настроек с BOM
    [IO.File]::WriteAllText($ConfigPath, (($out -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
}

function New-KvnboxShortcut {
    param(
        [Parameter(Mandatory)] [string]$Alias,
        [string]$Title = $Alias,
        [string]$ShortcutDir = [Environment]::GetFolderPath('Desktop'),
        [string]$DataDir = (Join-Path $env:LOCALAPPDATA 'kvnBox')
    )
    $ssh = Get-KvnboxTool 'ssh'
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $launcher = Join-Path $DataDir "$Alias.ps1"
    $body = @"
# kvnBox: меню сервера $Title (создано kvnbox.ps1)
`$Host.UI.RawUI.WindowTitle = 'kvnBox - $Title'
chcp 65001 > `$null
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
& '$ssh' -t '$Alias' menu
if (`$LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host "Подключение завершилось с ошибкой (код `$LASTEXITCODE)." -ForegroundColor Yellow
    Write-Host 'Если на сервере меняли порт SSH — запустите установку ярлыка снова.'
    Read-Host 'Нажмите Enter, чтобы закрыть окно'
}
"@
    # UTF-8 с BOM: Windows PowerShell 5.1 иначе читает файл в кодировке ANSI
    [IO.File]::WriteAllText($launcher, $body, [Text.UTF8Encoding]::new($true))

    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$launcher`""
    $wt = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
    New-Item -ItemType Directory -Force -Path $ShortcutDir | Out-Null
    $lnkPath = Join-Path $ShortcutDir "kvnBox - $Title.lnk"
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($lnkPath)
    if (Test-Path $wt) {
        $lnk.TargetPath = $wt
        $lnk.Arguments = "--title `"kvnBox - $Title`" `"$ps`" $psArgs"
    } else {
        $lnk.TargetPath = $ps
        $lnk.Arguments = $psArgs
    }
    $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,15"
    $lnk.Description = "kvnBox: меню сервера $Title"
    $lnk.Save()
    return [pscustomobject]@{ Shortcut = $lnkPath; Launcher = $launcher }
}

function Start-KvnboxSetup {
    Write-Host ''
    Write-Host '  kvnBox — ярлык для входа в меню сервера' -ForegroundColor Cyan
    Write-Host ''
    $ssh = Get-KvnboxTool 'ssh'
    if (-not $ssh -or -not (Get-KvnboxTool 'ssh-keygen')) {
        Write-Host '  Не найден клиент OpenSSH (ssh.exe).' -ForegroundColor Yellow
        Write-Host '  Установите: Параметры → Система → Дополнительные компоненты →'
        Write-Host '  «Клиент OpenSSH», затем запустите эту команду снова.'
        return
    }
    $server = ''
    while (-not $server) { $server = Read-KvnboxValue '  IP-адрес сервера' }
    $port = 0
    while ($port -lt 1 -or $port -gt 65535) {
        [int]::TryParse((Read-KvnboxValue '  Порт SSH' '22'), [ref]$port) | Out-Null
    }

    $alias = Get-KvnboxAlias $server
    $sshDir = Join-Path $env:USERPROFILE '.ssh'
    $key = Join-Path $sshDir "$alias"
    New-KvnboxKey $key
    $pub = (Get-Content -LiteralPath "$key.pub" -Raw).Trim()

    Write-Host ''
    Write-Host '  Добавляю ключ на сервер. Введите пароль root (один раз):' -ForegroundColor Cyan
    $remote = "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; " +
        "grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys; " +
        "command -v menu >/dev/null || echo KVNBOX_NOT_INSTALLED"
    $out = & $ssh -p $port -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "root@$server" $remote
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  Не удалось подключиться к серверу: проверьте IP, порт и пароль.' -ForegroundColor Red
        return
    }
    if ($out -match 'KVNBOX_NOT_INSTALLED') {
        Write-Host '  Внимание: kvnBox на сервере не найден — сначала установите панель.' -ForegroundColor Yellow
    }

    Set-KvnboxSshConfig -ConfigPath (Join-Path $sshDir 'config') -Alias $alias `
        -Server $server -Port $port -KeyPath $key
    & $ssh -o BatchMode=yes -o ConnectTimeout=15 $alias true
    if ($LASTEXITCODE -ne 0) {
        Write-Host '  Ключ не сработал: вход по ключу не прошёл.' -ForegroundColor Red
        return
    }

    $r = New-KvnboxShortcut -Alias $alias -Title $server
    Write-Host ''
    Write-Host '  Готово:' -ForegroundColor Green
    Write-Host "   - ключ SSH: $key"
    Write-Host "   - ярлык на рабочем столе: «$(Split-Path $r.Shortcut -Leaf)»"
    Write-Host "   - вход из терминала: ssh $alias"
    Write-Host ''
    $answer = Read-KvnboxValue '  Открыть меню сервера сейчас? (y/n)' 'y'
    if ($answer -match '^[yYдД]') {
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -NoProfile -ExecutionPolicy Bypass -File $r.Launcher
    }
}

if (-not $env:KVNBOX_SETUP_TEST) { Start-KvnboxSetup }
