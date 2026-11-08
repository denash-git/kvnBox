# kvnBox для Windows: список серверов kvnBox и вход в их меню без пароля.
#
# Установка и обновление (PowerShell):
#   irm https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1 | iex
#
# Приложение ставится в %LOCALAPPDATA%\kvnBox, ярлык «kvnBox» — на рабочий стол и в меню Пуск.
# Серверы: ключ SSH создаётся на ПК, добавляется на сервер (пароль root нужен один раз),
# сервер прописывается в ~\.ssh\config (вход `ssh kvnbox-<IP>` работает и из терминала).

$KvnboxVersion = '2.0.0'

$ErrorActionPreference = 'Stop'
$RawUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1'

function Get-EnvOr([string]$Name, [string]$Default) {
    $v = [Environment]::GetEnvironmentVariable($Name)
    if ($v) { return $v }
    return $Default
}

# KVNBOX_* — только для проверки на временных папках
$AppDir = Get-EnvOr 'KVNBOX_HOME' (Join-Path $env:LOCALAPPDATA 'kvnBox')
$SshDir = Get-EnvOr 'KVNBOX_SSH_DIR' (Join-Path $env:USERPROFILE '.ssh')
$DesktopDir = Get-EnvOr 'KVNBOX_DESKTOP' ([Environment]::GetFolderPath('Desktop'))
$StartMenuDir = Get-EnvOr 'KVNBOX_STARTMENU' ([Environment]::GetFolderPath('Programs'))
$AppScript = Join-Path $AppDir 'kvnbox.ps1'
$ServersFile = Join-Path $AppDir 'servers.json'
$UpdateFile = Join-Path $AppDir 'update.json'
$SshConfig = Join-Path $SshDir 'config'
$KnownHosts = [Environment]::GetEnvironmentVariable('KVNBOX_KNOWN_HOSTS')

# ═══ инструменты ═════════════════════════════════════════════════════════════

function Get-Tool([string]$Name) {
    $cmd = Get-Command "$Name.exe" -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $path = Join-Path $env:SystemRoot "System32\OpenSSH\$Name.exe"
    if (Test-Path $path) { return $path }
    return $null
}

function Get-PowerShellExe {
    return (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
}

function Write-Utf8([string]$Path, [string]$Text, [bool]$Bom = $false) {
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($Bom))
}

function Get-SshBase {
    $base = @('-F', $SshConfig)
    if ($KnownHosts) { $base += @('-o', "UserKnownHostsFile=$KnownHosts") }
    return $base
}

function Invoke-Ssh {
    # команда на сервере через ключ, без вопросов; $null — сервер недоступен
    param([string]$Alias, [string]$Command, [int]$Timeout = 10)
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    $out = & $ssh @base -o BatchMode=yes -o "ConnectTimeout=$Timeout" $Alias $Command 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return (@($out) -join "`n")
}

# ═══ серверы: список, ключи, ~\.ssh\config ═══════════════════════════════════

function Get-Alias([string]$Server) {
    return 'kvnbox-' + (($Server.ToLower()) -replace '[^a-z0-9.-]', '-')
}

function Get-Servers {
    $list = @()
    if (Test-Path $ServersFile) {
        $data = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($ServersFile))
        foreach ($s in $data) { $list += $s }
    }
    return $list
}

function Save-Servers($List) {
    Write-Utf8 $ServersFile (ConvertTo-Json -InputObject @($List) -Depth 3)
}

function New-ServerKey([string]$KeyPath) {
    # ключ без парольной фразы: меню открывается без вопросов
    if (Test-Path $KeyPath) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path $KeyPath) | Out-Null
    $argLine = "-q -t ed25519 -N `"`" -C `"kvnbox-$env:COMPUTERNAME`" -f `"$KeyPath`""
    $p = Start-Process -FilePath (Get-Tool 'ssh-keygen') -ArgumentList $argLine -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0 -or -not (Test-Path $KeyPath)) { throw 'Не удалось создать ключ SSH.' }
}

function Read-SshConfigLines {
    if (Test-Path $SshConfig) { return [IO.File]::ReadAllLines($SshConfig) }
}

function Remove-SshConfigHost([string]$Alias) {
    $out = New-Object System.Collections.Generic.List[string]
    $skip = $false
    foreach ($line in (Read-SshConfigLines)) {
        if ($line -match '^\s*(Host|Match)\s') { $skip = ($line.Trim() -eq "Host $Alias") }
        if (-not $skip) { $out.Add($line) }
    }
    while ($out.Count -gt 0 -and -not $out[$out.Count - 1].Trim()) { $out.RemoveAt($out.Count - 1) }
    return , $out
}

function Set-SshConfigHost([string]$Alias, [string]$Server, [int]$Port, [string]$KeyPath) {
    $out = Remove-SshConfigHost $Alias
    if ($out.Count -gt 0) { $out.Add('') }
    $out.AddRange([string[]]@(
            "Host $Alias",
            "    HostName $Server",
            "    Port $Port",
            "    User root",
            "    IdentityFile `"$KeyPath`"",
            "    IdentitiesOnly yes",
            "    ServerAliveInterval 30"
        ))
    # без BOM: OpenSSH не читает файл настроек с BOM
    Write-Utf8 $SshConfig (($out -join "`n") + "`n")
}

function Clear-SshConfigHost([string]$Alias) {
    $out = Remove-SshConfigHost $Alias
    $text = if ($out.Count) { ($out -join "`n") + "`n" } else { '' }
    Write-Utf8 $SshConfig $text
}

function Import-OldServers {
    # серверы, добавленные первой версией (записи kvnbox-* в ~\.ssh\config)
    $list = @(Get-Servers)
    $known = @($list | ForEach-Object { $_.Alias })
    $cur = $null
    $found = @()
    foreach ($line in (Read-SshConfigLines)) {
        if ($line -match '^\s*Host\s+(kvnbox-\S+)\s*$') {
            $cur = [ordered]@{ Alias = $Matches[1]; Host = ''; Port = 22; Key = '' }
            $found += $cur
        } elseif ($line -match '^\s*(Host|Match)\s') {
            $cur = $null
        } elseif ($cur) {
            if ($line -match '^\s*HostName\s+(\S+)') { $cur.Host = $Matches[1] }
            if ($line -match '^\s*Port\s+(\d+)') { $cur.Port = [int]$Matches[1] }
            if ($line -match '^\s*IdentityFile\s+"?([^"]+)"?') { $cur.Key = $Matches[1].Trim() }
        }
    }
    $added = $false
    foreach ($f in $found) {
        if ($f.Host -and $known -notcontains $f.Alias) {
            $list += [pscustomobject]@{
                Name = $f.Host; Host = $f.Host; Port = $f.Port; Alias = $f.Alias; Key = $f.Key
            }
            $added = $true
        }
    }
    if ($added) { Save-Servers $list }
}

# ═══ ярлыки ══════════════════════════════════════════════════════════════════

function New-Shortcut([string]$Path, [string]$Title, [string]$ExtraArgs = '') {
    $ps = Get-PowerShellExe
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$AppScript`" $ExtraArgs".Trim()
    $wt = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
    New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    if (Test-Path $wt) {
        $lnk.TargetPath = $wt
        $lnk.Arguments = "--title `"$Title`" `"$ps`" $psArgs"
    } else {
        $lnk.TargetPath = $ps
        $lnk.Arguments = $psArgs
    }
    $lnk.WorkingDirectory = $env:USERPROFILE
    $lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,15"
    $lnk.Description = $Title
    $lnk.Save()
}

function Get-SafeName([string]$Name) { return ($Name -replace '[\\/:*?"<>|]', '_') }

function Get-ServerShortcut($s) {
    return (Join-Path $DesktopDir ("kvnBox - " + (Get-SafeName $s.Name) + ".lnk"))
}

function Remove-OldShortcuts {
    # первая версия: ярлык и файл запуска на каждый сервер (kvnbox-<IP>.ps1)
    if (Test-Path $DesktopDir) {
        $shell = New-Object -ComObject WScript.Shell
        foreach ($f in Get-ChildItem -LiteralPath $DesktopDir -Filter 'kvnBox - *.lnk') {
            $args0 = $shell.CreateShortcut($f.FullName).Arguments
            if ($args0 -match 'kvnbox-[^"\\]+\.ps1') { Remove-Item -LiteralPath $f.FullName -Force }
        }
    }
    Get-ChildItem -LiteralPath $AppDir -Filter 'kvnbox-*.ps1' -ErrorAction SilentlyContinue |
        Remove-Item -Force
}

# ═══ установка и обновление приложения ═══════════════════════════════════════

function Get-RemoteScript([int]$TimeoutSec = 15) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $req = [Net.WebRequest]::Create("$RawUrl`?t=" + [DateTime]::UtcNow.Ticks)
    $req.Timeout = $TimeoutSec * 1000
    $resp = $req.GetResponse()
    try {
        $reader = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
        return $reader.ReadToEnd()
    } finally { $resp.Close() }
}

function Get-ScriptVersion([string]$Text) {
    if ($Text -match "(?m)^\`$KvnboxVersion\s*=\s*'([0-9.]+)'") { return $Matches[1] }
    return $null
}

function Test-ScriptText([string]$Text) {
    $errs = $null
    [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$null, [ref]$errs) | Out-Null
    return ($errs.Count -eq 0 -and (Get-ScriptVersion $Text))
}

function Install-App([string]$Text) {
    if (-not (Test-ScriptText $Text)) { throw 'Файл приложения повреждён — установка отменена.' }
    # UTF-8 с BOM: Windows PowerShell 5.1 иначе читает файл в кодировке ANSI
    Write-Utf8 $AppScript $Text $true
    New-Shortcut (Join-Path $DesktopDir 'kvnBox.lnk') 'kvnBox'
    New-Shortcut (Join-Path $StartMenuDir 'kvnBox.lnk') 'kvnBox'
    Import-OldServers
    Remove-OldShortcuts
}

function Get-UpdateInfo([switch]$Force) {
    # раз в сутки спрашиваем GitHub; без интернета — тихо
    $state = $null
    if (Test-Path $UpdateFile) {
        try { $state = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($UpdateFile)) } catch { }
    }
    $fresh = $state -and ([DateTime]::UtcNow - [DateTime]$state.Checked).TotalHours -lt 24
    if (-not $Force -and $fresh) { return $state.Latest }
    try {
        $latest = Get-ScriptVersion (Get-RemoteScript 3)
    } catch { return $(if ($state) { $state.Latest } else { $null }) }
    $obj = [pscustomobject]@{ Checked = [DateTime]::UtcNow.ToString('o'); Latest = $latest }
    Write-Utf8 $UpdateFile (ConvertTo-Json -InputObject $obj)
    return $latest
}

function Test-Newer([string]$Latest) {
    if (-not $Latest) { return $false }
    try { return ([version]$Latest -gt [version]$KvnboxVersion) } catch { return $false }
}

# ═══ экран ═══════════════════════════════════════════════════════════════════

$script:Notice = ''

function Show-Header([string]$Title, [string]$Badge = '') {
    Clear-Host
    $w = [Math]::Min([Console]::WindowWidth - 1, 72)
    $right = "v$KvnboxVersion"
    Write-Host ''
    Write-Host '  kvnBox' -ForegroundColor Cyan -NoNewline
    Write-Host "  ·  $Title" -NoNewline
    $pad = $w - 2 - 'kvnBox'.Length - 5 - $Title.Length - $right.Length - $Badge.Length - 2
    Write-Host (' ' * [Math]::Max(1, $pad)) -NoNewline
    if ($Badge) { Write-Host $Badge -ForegroundColor Yellow -NoNewline; Write-Host '  ' -NoNewline }
    Write-Host $right -ForegroundColor DarkGray
    Write-Host ('  ' + ('─' * ($w - 2))) -ForegroundColor DarkGray
    Write-Host ''
    if ($script:Notice) {
        Write-Host "  $($script:Notice)" -ForegroundColor Green
        Write-Host ''
        $script:Notice = ''
    }
}

function Select-Item {
    # стрелки ↑↓, Enter; Esc → -1; ключи из $Keys → @{Key; Index}
    param([string[]]$Items, [string]$Hint, [int]$Start = 0, [string[]]$Keys = @(), [int[]]$Dim = @())
    $top = [Console]::CursorTop
    $sel = [Math]::Max(0, [Math]::Min($Start, $Items.Count - 1))
    $w = [Math]::Min([Console]::WindowWidth - 1, 72)
    while ($true) {
        [Console]::SetCursorPosition(0, $top)
        for ($i = 0; $i -lt $Items.Count; $i++) {
            $text = $Items[$i]
            if ($text -eq '-') { Write-Host ''; continue }
            $line = ("    $text").PadRight($w)
            if ($i -eq $sel) {
                Write-Host '  ' -NoNewline
                Write-Host ("▶ $text ").PadRight($w - 2) -ForegroundColor Black -BackgroundColor Cyan
            } elseif ($Dim -contains $i) {
                Write-Host $line -ForegroundColor DarkGray
            } else {
                Write-Host $line
            }
        }
        Write-Host ''
        Write-Host ("  $Hint").PadRight($w) -ForegroundColor DarkGray
        $k = [Console]::ReadKey($true)
        switch ($k.Key) {
            'UpArrow' { do { $sel = ($sel - 1 + $Items.Count) % $Items.Count } while ($Items[$sel] -eq '-') }
            'DownArrow' { do { $sel = ($sel + 1) % $Items.Count } while ($Items[$sel] -eq '-') }
            'Home' { $sel = 0 }
            'End' { $sel = $Items.Count - 1 }
            'Enter' { return @{ Key = 'Enter'; Index = $sel } }
            'Escape' { return @{ Key = 'Escape'; Index = -1 } }
            default {
                if ($Keys -contains [string]$k.Key) { return @{ Key = [string]$k.Key; Index = $sel } }
            }
        }
    }
}

function Read-Value([string]$Prompt, [string]$Default = '') {
    $text = if ($Default) { "  $Prompt [$Default]" } else { "  $Prompt" }
    $v = (Read-Host $text).Trim()
    if ($v) { return $v }
    return $Default
}

function Read-YesNo([string]$Prompt, [bool]$Default = $false) {
    $hint = if ($Default) { 'Y/n' } else { 'y/N' }
    $v = (Read-Host "  $Prompt [$hint]").Trim()
    if (-not $v) { return $Default }
    return ($v -match '^[yYдД]')
}

function Pause-Key([string]$Text = 'Нажмите любую клавишу...') {
    Write-Host ''
    Write-Host "  $Text" -ForegroundColor DarkGray
    [Console]::ReadKey($true) | Out-Null
}

function Write-Ok([string]$t) { Write-Host "  ✓ $t" -ForegroundColor Green }
function Write-Warn([string]$t) { Write-Host "  ! $t" -ForegroundColor Yellow }
function Write-Fail([string]$t) { Write-Host "  ✗ $t" -ForegroundColor Red }
function Write-Step([string]$t) { Write-Host "  … $t" -ForegroundColor DarkGray }

# ═══ действия ════════════════════════════════════════════════════════════════

function Enter-Server($s) {
    Clear-Host
    $Host.UI.RawUI.WindowTitle = "kvnBox - $($s.Name)"
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    & $ssh @base -t $s.Alias menu
    $code = $LASTEXITCODE
    $Host.UI.RawUI.WindowTitle = 'kvnBox'
    if ($code -ne 0) {
        Write-Host ''
        Write-Fail "Подключение к «$($s.Name)» завершилось с ошибкой (код $code)."
        Write-Host '    Если на сервере меняли порт SSH — «Управление → Изменить порт SSH».'
        Pause-Key
    }
}

function Add-Server {
    Show-Header 'добавление сервера'
    Write-Host '  Нужен сервер с установленным kvnBox и пароль root (вводится один раз).'
    Write-Host '  Пустой ввод — отмена.' -ForegroundColor DarkGray
    Write-Host ''
    $server = Read-Value 'IP-адрес сервера'
    if (-not $server) { return }
    $port = 0
    while ($port -lt 1 -or $port -gt 65535) {
        [int]::TryParse((Read-Value 'Порт SSH' '22'), [ref]$port) | Out-Null
    }
    $name = Read-Value 'Название' $server

    $alias = Get-Alias $server
    $key = Join-Path $SshDir $alias
    Write-Host ''
    Write-Step 'Создание ключа SSH на этом компьютере'
    New-ServerKey $key
    $pub = ([IO.File]::ReadAllText("$key.pub")).Trim()

    Write-Host ''
    Write-Host '  Добавление ключа на сервер. Введите пароль root:' -ForegroundColor Cyan
    $remote = "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; " +
        "grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys; " +
        "command -v menu >/dev/null || echo KVNBOX_NOT_INSTALLED"
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    $out = & $ssh @base -p $port -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 `
        "root@$server" $remote
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Fail 'Не удалось подключиться: проверьте IP, порт и пароль.'
        Pause-Key
        return
    }

    Set-SshConfigHost $alias $server $port $key
    if ($null -eq (Invoke-Ssh $alias 'true' 15)) {
        Write-Fail 'Ключ добавлен, но вход по нему не прошёл.'
        Pause-Key
        return
    }
    $list = @(Get-Servers | Where-Object { $_.Alias -ne $alias })
    $s = [pscustomobject]@{ Name = $name; Host = $server; Port = $port; Alias = $alias; Key = $key }
    $list += $s
    Save-Servers $list
    Write-Ok "Сервер «$name» добавлен, вход по ключу работает."
    if (@($out) -match 'KVNBOX_NOT_INSTALLED') {
        Write-Warn 'kvnBox на сервере не найден — установите панель, затем входите.'
    }

    Write-Host ''
    Write-Host '  Вход по паролю можно отключить: на сервер смогут войти только по ключу.'
    Write-Host '  Ключ хранится на этом компьютере — не потеряйте его (папка .ssh).' -ForegroundColor DarkGray
    if (Read-YesNo 'Отключить вход по паролю сейчас?') { Set-PasswordLogin $s $false | Out-Null }
    $script:Notice = "Сервер «$name» добавлен."
    Pause-Key
}

function Get-ServerState($s) {
    # @{ Password = $true/$false; Keys = N } или $null
    $text = Invoke-Ssh $s.Alias 'kvnbox ssh status'
    if ($null -eq $text) { return $null }
    $pw = $null
    if ($text -match 'вход по паролю:\s*(включен|отключен)') { $pw = ($Matches[1] -eq 'включен') }
    $keys = @($text -split "`n" | Where-Object { $_ -match '^\s*#\d+' }).Count
    return @{ Password = $pw; Keys = $keys }
}

function Set-PasswordLogin($s, [bool]$Enabled) {
    $mode = if ($Enabled) { 'password' } else { 'key' }
    Write-Step 'Применение на сервере'
    $text = Invoke-Ssh $s.Alias "kvnbox ssh auth $mode" 20
    if ($null -eq $text) {
        Write-Fail 'Не удалось изменить: сервер недоступен или команда не выполнена.'
        return $false
    }
    Write-Ok (($text.Trim()) -replace '^✓\s*', '')
    return $true
}

function Remove-Server($s) {
    Show-Header "удаление «$($s.Name)»"
    if (-not (Read-YesNo "Удалить сервер «$($s.Name)» из списка?")) { return $false }
    Write-Host ''
    Write-Step 'Проверка сервера'
    $state = Get-ServerState $s
    $pub = $null
    if (Test-Path "$($s.Key).pub") { $pub = ([IO.File]::ReadAllText("$($s.Key).pub")).Trim() }
    if ($null -eq $state) {
        Write-Warn 'Сервер недоступен — ключ этого компьютера на нём останется.'
    } elseif ($state.Password -eq $false -and $state.Keys -le 1) {
        Write-Warn 'Ключ на сервере оставлен: он единственный, а вход по паролю отключён.'
        Write-Host '    Иначе на сервер нельзя будет войти.' -ForegroundColor DarkGray
    } elseif ($pub) {
        $body = ($pub -split '\s+')[1]
        $cmd = 'f=~/.ssh/authorized_keys; grep -vF ''' + $body + ''' "$f" > "$f.kvnbox"; ' +
            'cat "$f.kvnbox" > "$f"; rm -f "$f.kvnbox"'
        if ($null -ne (Invoke-Ssh $s.Alias $cmd)) { Write-Ok 'Ключ удалён с сервера.' }
        else { Write-Warn 'Не удалось удалить ключ с сервера.' }
    }
    Clear-SshConfigHost $s.Alias
    foreach ($f in @($s.Key, "$($s.Key).pub")) {
        if ($f -and (Test-Path -LiteralPath $f)) { Remove-Item -LiteralPath $f -Force }
    }
    $lnk = Get-ServerShortcut $s
    if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }
    Save-Servers @(Get-Servers | Where-Object { $_.Alias -ne $s.Alias })
    Write-Ok "Сервер «$($s.Name)» удалён с этого компьютера."
    $script:Notice = "Сервер «$($s.Name)» удалён."
    Pause-Key
    return $true
}

function Update-Server($s) {
    $list = @(Get-Servers | ForEach-Object { if ($_.Alias -eq $s.Alias) { $s } else { $_ } })
    Save-Servers $list
}

function Show-ServerMenu($s) {
    $state = $null
    $checked = $false
    $sel = 0
    while ($true) {
        Show-Header "сервер «$($s.Name)»"
        Write-Host "  Адрес: $($s.Host):$($s.Port)    Вход из терминала: ssh $($s.Alias)" -ForegroundColor DarkGray
        if (-not $checked) {
            Write-Step 'Проверка сервера'
            $state = Get-ServerState $s
            $checked = $true
            Show-Header "сервер «$($s.Name)»"
            Write-Host "  Адрес: $($s.Host):$($s.Port)    Вход из терминала: ssh $($s.Alias)" -ForegroundColor DarkGray
        }
        if ($null -eq $state) {
            Write-Host '  Сервер не отвечает по ключу.' -ForegroundColor Yellow
        } elseif ($null -ne $state.Password) {
            $pwText = if ($state.Password) { 'включен' } else { 'отключен (только ключ)' }
            Write-Host "  Вход по паролю: $pwText" -ForegroundColor DarkGray
        }
        Write-Host ''
        $pwItem = if ($state -and $state.Password -eq $false) { 'Включить вход по паролю' } else { 'Отключить вход по паролю' }
        $lnkItem = if (Test-Path -LiteralPath (Get-ServerShortcut $s)) { 'Убрать ярлык с рабочего стола' } else { 'Ярлык этого сервера на рабочий стол' }
        $items = @('Войти в меню сервера', $pwItem, 'Переименовать', 'Изменить порт SSH', $lnkItem, 'Удалить сервер', '-', 'Назад')
        $r = Select-Item $items '↑↓ выбор   Enter открыть   Esc назад' $sel
        if ($r.Key -eq 'Escape') { return }
        $sel = $r.Index
        switch ($r.Index) {
            0 { Enter-Server $s; $checked = $false }
            1 {
                Show-Header "сервер «$($s.Name)»"
                if ($null -eq $state) { Write-Fail 'Сервер не отвечает.'; Pause-Key; $checked = $false; break }
                $enable = ($state.Password -eq $false)
                if (-not $enable) {
                    Write-Host '  После отключения войти на сервер можно будет только по ключу.'
                    Write-Host '  Ключ этого компьютера уже работает.' -ForegroundColor DarkGray
                    if (-not (Read-YesNo 'Отключить вход по паролю?')) { break }
                }
                Set-PasswordLogin $s $enable | Out-Null
                Pause-Key
                $checked = $false
            }
            2 {
                Show-Header "сервер «$($s.Name)»"
                $new = Read-Value 'Новое название' $s.Name
                if ($new -and $new -ne $s.Name) {
                    $oldLnk = Get-ServerShortcut $s
                    $hadLnk = Test-Path -LiteralPath $oldLnk
                    $s.Name = $new
                    Update-Server $s
                    if ($hadLnk) {
                        Remove-Item -LiteralPath $oldLnk -Force
                        New-Shortcut (Get-ServerShortcut $s) "kvnBox - $($s.Name)" "-Connect $($s.Alias)"
                    }
                    $script:Notice = 'Название изменено.'
                }
            }
            3 {
                Show-Header "сервер «$($s.Name)»"
                Write-Host '  Только запись на этом компьютере — если порт SSH уже сменили на сервере.'
                Write-Host '  (Сменить порт на сервере: меню сервера → Настройки → Управление SSH.)' -ForegroundColor DarkGray
                Write-Host ''
                $p = 0
                [int]::TryParse((Read-Value 'Порт SSH' ([string]$s.Port)), [ref]$p) | Out-Null
                if ($p -ge 1 -and $p -le 65535 -and $p -ne $s.Port) {
                    $s.Port = $p
                    Update-Server $s
                    Set-SshConfigHost $s.Alias $s.Host $s.Port $s.Key
                    $script:Notice = "Порт изменён на $p."
                    $checked = $false
                }
            }
            4 {
                $lnk = Get-ServerShortcut $s
                if (Test-Path -LiteralPath $lnk) {
                    Remove-Item -LiteralPath $lnk -Force
                    $script:Notice = 'Ярлык убран.'
                } else {
                    New-Shortcut $lnk "kvnBox - $($s.Name)" "-Connect $($s.Alias)"
                    $script:Notice = "Ярлык «$(Split-Path $lnk -Leaf)» на рабочем столе."
                }
            }
            5 { if (Remove-Server $s) { return } }
            7 { return }
        }
    }
}

function Update-App {
    Show-Header 'обновление'
    Write-Step 'Загрузка новой версии'
    try {
        $text = Get-RemoteScript
        Install-App $text
    } catch {
        Write-Fail "Обновить не удалось: $($_.Exception.Message)"
        Pause-Key
        return $false
    }
    Write-Ok "Установлена версия $(Get-ScriptVersion $text). Перезапуск..."
    Start-Sleep -Milliseconds 800
    & (Get-PowerShellExe) -NoProfile -ExecutionPolicy Bypass -File $AppScript
    return $true
}

function Start-App {
    $Host.UI.RawUI.WindowTitle = 'kvnBox'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
    if (-not (Get-Tool 'ssh') -or -not (Get-Tool 'ssh-keygen')) {
        Show-Header 'серверы'
        Write-Fail 'Не найден клиент OpenSSH (ssh.exe).'
        Write-Host '  Установите: Параметры → Система → Дополнительные компоненты → «Клиент OpenSSH».'
        Pause-Key
        return
    }
    $latest = Get-UpdateInfo
    $sel = 0
    while ($true) {
        $servers = @(Get-Servers)
        $newer = Test-Newer $latest
        $badge = if ($newer) { "↑ $latest" } else { '' }
        Show-Header 'мои серверы' $badge
        $items = @()
        foreach ($s in $servers) { $items += ('{0,-28} {1}' -f $s.Name, "$($s.Host):$($s.Port)") }
        if (-not $servers.Count) {
            Write-Host '  Серверов пока нет — добавьте первый.' -ForegroundColor DarkGray
            Write-Host ''
        }
        if ($servers.Count) { $items += '-' }
        $iAdd = $items.Count; $items += '+ Добавить сервер'
        $iUpd = -1
        if ($newer) { $iUpd = $items.Count; $items += "↑ Обновить приложение до $latest" }
        $iExit = $items.Count; $items += 'Выход'
        $hint = if ($servers.Count) { '↑↓ выбор   Enter войти   → управление   Esc выход' } else { '↑↓ выбор   Enter   Esc выход' }
        $r = Select-Item $items $hint $sel @('RightArrow')
        if ($r.Key -eq 'Escape') { Clear-Host; return }
        $sel = $r.Index
        if ($r.Index -lt $servers.Count) {
            if ($r.Key -eq 'RightArrow') { Show-ServerMenu $servers[$r.Index] }
            else { Enter-Server $servers[$r.Index] }
        } elseif ($r.Key -ne 'Enter') {
            continue
        } elseif ($r.Index -eq $iAdd) {
            Add-Server
        } elseif ($r.Index -eq $iUpd) {
            if (Update-App) { return }
        } elseif ($r.Index -eq $iExit) {
            Clear-Host
            return
        }
    }
}

# ═══ запуск ══════════════════════════════════════════════════════════════════

function Start-Kvnbox([string[]]$CliArgs) {
    $self = $PSCommandPath
    $installed = $self -and ((Resolve-Path -LiteralPath $self).Path -eq [IO.Path]::GetFullPath($AppScript))
    if (-not $installed) {
        # первый запуск (irm | iex) или файл скачан вручную — установка и сразу приложение
        Write-Host ''
        Write-Host '  kvnBox — установка приложения...' -ForegroundColor Cyan
        $text = if ($self) { [IO.File]::ReadAllText($self, [Text.Encoding]::UTF8) } else { Get-RemoteScript }
        Install-App $text
        Write-Host '  ✓ Ярлык «kvnBox» — на рабочем столе и в меню Пуск.' -ForegroundColor Green
        Start-Sleep -Milliseconds 800
    }
    if ($CliArgs.Count -ge 2 -and $CliArgs[0] -eq '-Connect') {
        $s = @(Get-Servers | Where-Object { $_.Alias -eq $CliArgs[1] })
        if ($s.Count) { Enter-Server $s[0]; return }
    }
    Start-App
}

if (-not $env:KVNBOX_SETUP_TEST) { Start-Kvnbox $args }
