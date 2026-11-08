# kvnBox для Windows: список серверов kvnBox и вход в их меню без пароля.
#
# Установка и обновление (PowerShell):
#   irm https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1 | iex
#
# Приложение ставится в %LOCALAPPDATA%\kvnBox, ярлык «kvnBox» — на рабочий стол и в меню Пуск.
# Серверы: ключ SSH создаётся на ПК, добавляется на сервер (пароль root нужен один раз),
# сервер прописывается в ~\.ssh\config (вход `ssh kvnbox-<IP>` работает и из терминала).

$KvnboxVersion = '2.2.0'

$ErrorActionPreference = 'Stop'
$RawUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1'
$IconUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ico'

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
$AppIcon = Join-Path $AppDir 'kvnbox.ico'
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
    $lnk.IconLocation = if (Test-Path $AppIcon) { "$AppIcon,0" } else { "$env:SystemRoot\System32\shell32.dll,15" }
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

function Install-Icon([string]$SourceDir = '') {
    # значок: рядом со скриптом (файл скачан вручную) или с GitHub; не вышло — системный
    try {
        $local = if ($SourceDir) { Join-Path $SourceDir 'kvnbox.ico' } else { '' }
        if ($local -and (Test-Path $local) -and ([IO.Path]::GetFullPath($local) -ne [IO.Path]::GetFullPath($AppIcon))) {
            $bytes = [IO.File]::ReadAllBytes($local)
        } else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $bytes = (New-Object Net.WebClient).DownloadData("$IconUrl`?t=" + [DateTime]::UtcNow.Ticks)
        }
        # проверка: заголовок файла .ico (00 00 01 00)
        if ($bytes.Length -gt 6 -and $bytes[0] -eq 0 -and $bytes[1] -eq 0 -and $bytes[2] -eq 1 -and $bytes[3] -eq 0) {
            New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
            [IO.File]::WriteAllBytes($AppIcon, $bytes)
        }
    } catch { }
}

function Update-ServerShortcuts {
    # ярлыки серверов (kvnBox - имя.lnk → -Connect) — с текущим значком
    foreach ($s in @(Get-Servers)) {
        $lnk = Get-ServerShortcut $s
        if (Test-Path -LiteralPath $lnk) { New-Shortcut $lnk "kvnBox - $($s.Name)" "-Connect $($s.Alias)" }
    }
}

function Install-App([string]$Text, [string]$SourceDir = '') {
    if (-not (Test-ScriptText $Text)) { throw 'Файл приложения повреждён — установка отменена.' }
    # UTF-8 с BOM: Windows PowerShell 5.1 иначе читает файл в кодировке ANSI
    Write-Utf8 $AppScript $Text $true
    Install-Icon $SourceDir
    New-Shortcut (Join-Path $DesktopDir 'kvnBox.lnk') 'kvnBox'
    New-Shortcut (Join-Path $StartMenuDir 'kvnBox.lnk') 'kvnBox'
    Import-OldServers
    Remove-OldShortcuts
    Update-ServerShortcuts
}

function Get-UpdateInfo([switch]$Force) {
    # раз в сутки спрашиваем GitHub; без интернета — тихо
    $state = $null
    if (Test-Path $UpdateFile) {
        try { $state = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($UpdateFile)) } catch { }
    }
    $fresh = $state -and ([DateTime]::UtcNow - [DateTime]$state.Checked).TotalHours -lt 6
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

# ═══ экран (оформление как у меню kvnBox на сервере) ═════════════════════════

$script:Notice = ''
$Esc = [char]27
$Rst = "$Esc[0m"
$Col = @{
    Blue = "$Esc[1;34m"; Green = "$Esc[1;32m"; Red = "$Esc[1;31m"; Yellow = "$Esc[1;33m"
    Cyan = "$Esc[1;36m"; Dim = "$Esc[2m"; Bold = "$Esc[1m"
}
$Block = 60
$BackLabels = @('Назад', 'Отмена', 'Выход')
$MainTitle = "kvnBox v. $KvnboxVersion"

function Get-Plain([string]$t) { return ($t -replace "$([char]27)\[[0-9;]*[A-Za-z]", '') }
function Get-ScreenWidth { try { $w = [Console]::WindowWidth } catch { $w = 0 }; if ($w -lt 20) { $w = 100 }; return $w }
function Get-ScreenHeight { try { $h = [Console]::WindowHeight } catch { $h = 0 }; if ($h -lt 10) { $h = 30 }; return $h }
function Set-Cursor([bool]$Visible) { try { [Console]::CursorVisible = $Visible } catch { } }
function Read-MenuKey { return [Console]::ReadKey($true) }
function Get-BlockPad { return (' ' * [Math]::Max([Math]::Floor(((Get-ScreenWidth) - $Block) / 2), 2)) }

function Get-Centered([string]$t) {
    $pad = [Math]::Max([Math]::Floor(((Get-ScreenWidth) - (Get-Plain $t).Length) / 2), 0)
    return (' ' * $pad) + $t
}

function Get-HeaderLines([string]$Title, [string[]]$Sub = @(), [bool]$Badge = $false) {
    $t = $Title
    if ($Badge) { $t = "$t $($Col.Green)[↑ update]$Rst" }
    $lines = @((Get-Centered "$($Col.Blue)$('═' * $Block)$Rst"), (Get-Centered "$($Col.Yellow)$t$Rst"))
    foreach ($s in $Sub) { $lines += (Get-Centered $s) }
    $lines += (Get-Centered "$($Col.Blue)$('─' * $Block)$Rst")
    $lines += ''
    return $lines
}

function Show-Header([string]$Title, [string[]]$Sub = @()) {
    # экран с текстом и вводом (добавление, удаление, обновление)
    Set-Cursor $true
    [Console]::Write("$Esc[2J$Esc[H")
    foreach ($l in (Get-HeaderLines $Title $Sub)) { [Console]::WriteLine($l) }
    if ($script:Notice) {
        [Console]::WriteLine((Get-Centered "$($Col.Green)✓ $($script:Notice)$Rst"))
        [Console]::WriteLine('')
        $script:Notice = ''
    }
}

function Get-OptionLines([string[]]$Options, [int]$Active) {
    # как _draw_options на сервере: блок по центру, выбранный — инверсией,
    # «Назад/Выход» — жёлтым и с пустой строкой перед, «Удалить» — красным
    $plain = @($Options | ForEach-Object { Get-Plain $_ })
    $max = ($plain | Measure-Object -Property Length -Maximum).Maximum
    $indent = ' ' * [Math]::Max([Math]::Floor(((Get-ScreenWidth) - $max) / 2), 0)
    $out = @()
    for ($i = 0; $i -lt $Options.Count; $i++) {
        $opt = $Options[$i]; $p = $plain[$i]
        if ($opt -eq '-') { $out += ''; continue }  # пустая строка-разделитель
        $isBack = $BackLabels -contains $p
        $isDanger = $p -like '*Удалить*'
        if ($isBack -and $i) { $out += '' }
        if ($isBack -and $opt -notmatch "$([char]27)") { $opt = "$($Col.Yellow)$opt$Rst" }
        if ($isDanger -and $opt -notmatch "$([char]27)") { $opt = "$($Col.Red)$opt$Rst" }
        $gap = $max - $p.Length
        if ($isBack) {
            $l = ' ' * [Math]::Floor($gap / 2); $r = ' ' * ($gap - [Math]::Floor($gap / 2))
            if ($i -eq $Active) { $out += "$indent$Esc[43;30m  $l$p$r  $Esc[0m" }
            else { $out += "$indent  $l$opt$r  " }
        } else {
            $pad = ' ' * $gap
            if ($i -eq $Active -and $p.StartsWith('●')) {
                # сервер: точка остаётся цветной и на выделении
                $dot = if ($opt -match '1;32m●') { '32' } else { '31' }
                $out += "$indent$Esc[47;30m  $Esc[1;47;${dot}m●$Esc[22;47;30m$($p.Substring(1))$pad  $Esc[0m"
            } elseif ($i -eq $Active) {
                $style = if ($isDanger) { "$Esc[41;30m" } else { "$Esc[7m" }
                $out += "$indent$style  $p$pad  $Esc[0m"
            } else {
                $out += "$indent  $opt$pad  "
            }
        }
    }
    return $out
}

function Select-Menu {
    # меню стрелками: Enter → @{Key='Enter'; Index}; q/Esc → Index -1; 0 → Key='Exit'
    param(
        [string]$Title, [string[]]$Sub = @(), [bool]$Badge = $false, [string[]]$Info = @(),
        [string[]]$Options, [string]$Hint = '', [int]$Start = 0, [string[]]$Keys = @()
    )
    if (-not $Hint) { $Hint = '↑↓ — выбор   Enter — выбрать   q — назад   0 — Выход' }
    $n = $Options.Count
    $active = [Math]::Max(0, [Math]::Min($Start, $n - 1))
    while ($Options[$active] -eq '-') { $active = ($active + 1) % $n }
    $note = $script:Notice
    $script:Notice = ''
    Set-Cursor $false
    [Console]::Write("$Esc[2J$Esc[H")
    while ($true) {
        $lines = @(Get-HeaderLines $Title $Sub $Badge)
        if ($note) { $lines += (Get-Centered "$($Col.Green)✓ $note$Rst"); $lines += '' }
        if ($Info.Count) {
            $pad = Get-BlockPad
            foreach ($l in $Info) { $lines += "$pad$l" }
            $lines += ''
        }
        $lines += Get-OptionLines $Options $active
        $height = Get-ScreenHeight
        $frame = "$Esc[H" + (($lines | ForEach-Object { "$_$Esc[K" }) -join "`n") + "$Esc[J" +
            "$Esc[$height;1H$Esc[K" + (Get-Centered "$($Col.Dim)$Hint$Rst")
        [Console]::Write($frame)
        $k = Read-MenuKey
        $done = $null
        switch ($k.Key) {
            'UpArrow' { do { $active = ($active - 1 + $n) % $n } while ($Options[$active] -eq '-') }
            'DownArrow' { do { $active = ($active + 1) % $n } while ($Options[$active] -eq '-') }
            'Enter' { $done = @{ Key = 'Enter'; Index = $active } }
            'Escape' { $done = @{ Key = 'Back'; Index = -1 } }
            'Q' { $done = @{ Key = 'Back'; Index = -1 } }
            'D0' { $done = @{ Key = 'Exit'; Index = -1 } }
            'NumPad0' { $done = @{ Key = 'Exit'; Index = -1 } }
            default { if ($Keys -contains [string]$k.Key) { $done = @{ Key = [string]$k.Key; Index = $active } } }
        }
        if ($done) {
            [Console]::Write("$Esc[$height;1H$Esc[K$Esc[H")
            Set-Cursor $true
            return $done
        }
    }
}

function Read-Value([string]$Prompt, [string]$Default = '') {
    $d = if ($Default) { " [$Default]" } else { '' }
    [Console]::Write("$(Get-BlockPad)$Prompt$d`: ")
    $v = ([Console]::ReadLine()).Trim()
    if ($v) { return $v }
    return $Default
}

function Read-YesNo([string]$Prompt, [bool]$Default = $false) {
    $hint = if ($Default) { 'Y/n' } else { 'y/N' }
    [Console]::Write("$(Get-BlockPad)$Prompt ($hint): ")
    $v = ([Console]::ReadLine()).Trim()
    if (-not $v) { return $Default }
    return ($v -match '^[yYдД]')
}

function Pause-Key {
    [Console]::WriteLine('')
    [Console]::WriteLine((Get-Centered "$($Col.Dim)Нажмите любую клавишу для продолжения...$Rst"))
    Read-MenuKey | Out-Null
}

function Write-Line([string]$t = '') { [Console]::WriteLine("$(Get-BlockPad)$t") }
function Write-Note([string]$t) { Write-Line "$($Col.Dim)$t$Rst" }
function Write-Ok([string]$t) { [Console]::WriteLine((Get-Centered "$($Col.Green)✓ $t$Rst")) }
function Write-Warn([string]$t) { [Console]::WriteLine((Get-Centered "$($Col.Yellow)⚠ $t$Rst")) }
function Write-Fail([string]$t) { [Console]::WriteLine((Get-Centered "$($Col.Red)✗ $t$Rst")) }
function Write-Step([string]$t) { [Console]::WriteLine((Get-Centered "$($Col.Dim)[*] $t$Rst")) }

function Get-Online($Servers) {
    # доступность серверов: TCP до порта SSH, все сразу, не дольше ~0,8 с
    $res = @{}
    $probes = @()
    foreach ($s in @($Servers)) {
        $c = New-Object Net.Sockets.TcpClient
        $probes += @{ Alias = $s.Alias; Client = $c; Task = $c.ConnectAsync([string]$s.Host, [int]$s.Port) }
    }
    if ($probes.Count) {
        try { [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($probes | ForEach-Object { $_.Task }), 800) | Out-Null } catch { }
    }
    foreach ($p in $probes) {
        $res[$p.Alias] = ($p.Task.Status -eq 'RanToCompletion' -and $p.Client.Connected)
        $p.Client.Dispose()
    }
    return $res
}

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
        Write-Line
        Write-Fail "Подключение к «$($s.Name)» завершилось с ошибкой (код $code)."
        Write-Note '  Если на сервере меняли порт SSH — «Управление → Изменить порт SSH».'
        Pause-Key
    }
}

function Add-Server {
    Show-Header 'Добавление сервера'
    Write-Line 'Нужен сервер с установленным kvnBox и пароль root (вводится один раз).'
    Write-Note 'Пустой ввод — отмена.'
    Write-Line
    $server = Read-Value 'IP-адрес сервера'
    if (-not $server) { return }
    $port = 0
    while ($port -lt 1 -or $port -gt 65535) {
        [int]::TryParse((Read-Value 'Порт SSH' '22'), [ref]$port) | Out-Null
    }

    $alias = Get-Alias $server
    $key = Join-Path $SshDir $alias
    Write-Line
    Write-Step 'Создание ключа SSH на этом компьютере...'
    New-ServerKey $key
    $pub = ([IO.File]::ReadAllText("$key.pub")).Trim()

    Write-Line
    Write-Line "$($Col.Cyan)Добавление ключа на сервер. Введите пароль root:$Rst"
    $remote = "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; " +
        "grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys; " +
        "command -v menu >/dev/null || echo KVNBOX_NOT_INSTALLED; " +
        'echo "KVNBOX_FQDN=$(hostname -f 2>/dev/null || hostname)"'
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    $out = & $ssh @base -p $port -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 `
        "root@$server" $remote
    if ($LASTEXITCODE -ne 0) {
        Write-Line
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
    # название по умолчанию — FQDN сервера (как в меню сервера), иначе IP
    $fqdn = ''
    foreach ($line in @($out)) { if ($line -match '^KVNBOX_FQDN=(\S+)') { $fqdn = $Matches[1] } }
    if (-not $fqdn -or $fqdn -eq 'localhost') { $fqdn = $server }
    Write-Line
    $name = Read-Value 'Название' $fqdn
    $list = @(Get-Servers | Where-Object { $_.Alias -ne $alias })
    $s = [pscustomobject]@{ Name = $name; Host = $server; Port = $port; Alias = $alias; Key = $key }
    $list += $s
    Save-Servers $list
    Write-Ok "Сервер «$name» добавлен, вход по ключу работает."
    if (@($out) -match 'KVNBOX_NOT_INSTALLED') {
        Write-Warn 'kvnBox на сервере не найден — установите панель, затем входите.'
    }

    Write-Line
    Write-Line 'Вход по паролю можно отключить: на сервер смогут войти только по ключу.'
    Write-Note 'Ключ хранится на этом компьютере — не потеряйте его (папка .ssh).'
    if (Read-YesNo 'Отключить вход по паролю сейчас?') { Set-PasswordLogin $s $false | Out-Null }
    $script:Notice = "Сервер «$name» добавлен."
    Pause-Key
}

function Get-ServerFqdn($s) {
    $text = Invoke-Ssh $s.Alias 'hostname -f 2>/dev/null || hostname' 8
    if ($text) {
        $f = ($text -split "`n")[0].Trim()
        if ($f -and $f -ne 'localhost') { return $f }
    }
    return ''
}

function Update-DefaultNames($Online) {
    # серверы, названные по IP (добавлены первой версией), получают имя FQDN
    foreach ($s in @(Get-Servers)) {
        if ($s.Name -eq $s.Host -and $Online[$s.Alias]) {
            $f = Get-ServerFqdn $s
            if ($f) { $s.Name = $f; Update-Server $s }
        }
    }
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
    Write-Step 'Применение на сервере...'
    $text = Invoke-Ssh $s.Alias "kvnbox ssh auth $mode" 20
    if ($null -eq $text) {
        Write-Fail 'Не удалось изменить: сервер недоступен или команда не выполнена.'
        return $false
    }
    Write-Ok (($text.Trim()) -replace '^✓\s*', '')
    return $true
}

function Remove-Server($s) {
    Show-Header "Удаление сервера: $($s.Name)"
    if (-not (Read-YesNo "Удалить сервер «$($s.Name)» из списка?")) { return $false }
    Write-Line
    Write-Step 'Проверка сервера...'
    $state = Get-ServerState $s
    $pub = $null
    if (Test-Path "$($s.Key).pub") { $pub = ([IO.File]::ReadAllText("$($s.Key).pub")).Trim() }
    if ($null -eq $state) {
        Write-Warn 'Сервер недоступен — ключ этого компьютера на нём останется.'
    } elseif ($state.Password -eq $false -and $state.Keys -le 1) {
        Write-Warn 'Ключ на сервере оставлен: он единственный, а вход по паролю отключён.'
        Write-Note '  Иначе на сервер нельзя будет войти.'
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

function Get-InfoLine([string]$Label, [string]$Value) {
    return ('{0,-16}' -f $Label) + "$($Col.Bold)$Value$Rst"
}

function Show-ServerMenu($s) {
    # 'exit' — выйти из приложения (клавиша 0)
    $state = $null
    $checked = $false
    $online = $false
    $sel = 0
    while ($true) {
        $title = "Сервер: $($s.Name)"
        if (-not $checked) {
            Show-Header $title
            Write-Step 'Проверка сервера...'
            $state = Get-ServerState $s
            $online = ($null -ne $state) -or (Get-Online @($s))[$s.Alias]
            $checked = $true
        }
        $status = if ($null -ne $state) { "$($Col.Green)● на связи$Rst" }
        elseif ($online) { "$($Col.Yellow)● не входит по ключу$Rst" }
        else { "$($Col.Red)● не отвечает$Rst" }
        $info = @(
            (Get-InfoLine 'Адрес:' "$($s.Host):$($s.Port)"),
            (('{0,-16}' -f 'Состояние:') + $status)
        )
        if ($state -and $null -ne $state.Password) {
            $pw = if ($state.Password) { "$($Col.Yellow)включен$Rst" } else { "$($Col.Green)отключен$Rst (только ключ)" }
            $info += (('{0,-16}' -f 'Вход по паролю:') + $pw)
        }
        $info += (Get-InfoLine 'Терминал:' "ssh $($s.Alias)")
        $pwItem = if ($state -and $state.Password -eq $false) { 'Включить вход по паролю' } else { 'Отключить вход по паролю' }
        $lnkItem = if (Test-Path -LiteralPath (Get-ServerShortcut $s)) { 'Убрать ярлык с рабочего стола' } else { 'Ярлык сервера на рабочий стол' }
        $options = @('Войти в меню сервера', $pwItem, 'Переименовать', 'Изменить порт SSH', $lnkItem, 'Удалить сервер', 'Назад')
        $r = Select-Menu -Title $title -Info $info -Options $options -Start $sel -Keys @('LeftArrow')
        if ($r.Key -eq 'Exit') { return 'exit' }
        if ($r.Key -ne 'Enter') { return '' }
        $sel = $r.Index
        switch ($r.Index) {
            0 { Enter-Server $s; $checked = $false }
            1 {
                Show-Header $title
                if ($null -eq $state) { Write-Fail 'Сервер не отвечает.'; Pause-Key; $checked = $false; break }
                $enable = ($state.Password -eq $false)
                if (-not $enable) {
                    Write-Line 'После отключения войти на сервер можно будет только по ключу.'
                    Write-Note 'Ключ этого компьютера уже работает.'
                    Write-Line
                    if (-not (Read-YesNo 'Отключить вход по паролю?')) { break }
                    Write-Line
                }
                Set-PasswordLogin $s $enable | Out-Null
                Pause-Key
                $checked = $false
            }
            2 {
                Show-Header $title
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
                Show-Header $title
                Write-Line 'Только запись на этом компьютере — если порт SSH уже сменили на сервере.'
                Write-Note 'Сменить порт на сервере: меню сервера → Настройки → Управление SSH.'
                Write-Line
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
            5 { if (Remove-Server $s) { return '' } }
            6 { return '' }
        }
    }
}

function Update-App {
    Show-Header 'Обновление приложения'
    Write-Step 'Загрузка новой версии...'
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

function Show-ManageMenu($Online) {
    # выбор сервера для изменения/удаления → экран сервера
    $sel = 0
    while ($true) {
        $servers = @(Get-Servers)
        if (-not $servers.Count) { return '' }
        $nameW = 0
        foreach ($s in $servers) { $nameW = [Math]::Max($nameW, ([string]$s.Name).Length) }
        $options = @()
        foreach ($s in $servers) {
            $dot = if ($Online -and $Online[$s.Alias]) { "$($Col.Green)●$Rst" } else { "$($Col.Red)●$Rst" }
            $options += "$dot " + ([string]$s.Name).PadRight($nameW + 3) + "$($s.Host):$($s.Port)"
        }
        $options += 'Назад'
        $r = Select-Menu -Title 'Изменить / удалить сервер' -Options $options -Start $sel `
            -Info @("$($Col.Dim)Выберите сервер: вход по паролю, название, порт, ярлык, удаление.$Rst")
        if ($r.Key -eq 'Exit') { return 'exit' }
        if ($r.Key -ne 'Enter' -or $r.Index -ge $servers.Count) { return '' }
        $sel = $r.Index
        if ((Show-ServerMenu $servers[$r.Index]) -eq 'exit') { return 'exit' }
    }
}

function Start-App {
    $Host.UI.RawUI.WindowTitle = 'kvnBox'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
    if (-not (Get-Tool 'ssh') -or -not (Get-Tool 'ssh-keygen')) {
        Show-Header $MainTitle @('лаунчер управления')
        Write-Fail 'Не найден клиент OpenSSH (ssh.exe).'
        Write-Line 'Установите: Параметры → Система → Дополнительные компоненты → «Клиент OpenSSH».'
        Pause-Key
        return
    }
    $latest = Get-UpdateInfo
    $sel = 0
    $online = Get-Online @(Get-Servers)
    Update-DefaultNames $online
    try {
        while ($true) {
            $servers = @(Get-Servers)
            if ($null -eq $online) { $online = Get-Online $servers }
            $newer = Test-Newer $latest
            $nameW = 0
            foreach ($s in $servers) { $nameW = [Math]::Max($nameW, ([string]$s.Name).Length) }
            $options = @()
            foreach ($s in $servers) {
                $dot = if ($online[$s.Alias]) { "$($Col.Green)●$Rst" } else { "$($Col.Red)●$Rst" }
                $options += "$dot " + ([string]$s.Name).PadRight($nameW + 3) + "$($s.Host):$($s.Port)"
            }
            if ($servers.Count) { $options += '-' }
            $iAdd = $options.Count; $options += 'Добавить сервер'
            $iMng = -1
            if ($servers.Count) { $iMng = $options.Count; $options += 'Изменить / удалить сервер' }
            $iUpd = -1
            if ($newer) { $iUpd = $options.Count; $options += "Обновить приложение до $latest" }
            $options += 'Выход'
            $info = @()
            if (-not $servers.Count) {
                $info = @("$($Col.Dim)Серверов пока нет — добавьте первый.$Rst")
                if ($sel -eq 0) { $sel = $iAdd }
            }
            $hint = if ($servers.Count) { '↑↓ — выбор   Enter — войти   → — управление   q — выход' } else { '↑↓ — выбор   Enter — выбрать   q — выход' }
            $r = Select-Menu -Title $MainTitle -Sub @('лаунчер управления') -Badge $newer -Info $info `
                -Options $options -Hint $hint -Start $sel -Keys @('RightArrow')
            if ($r.Key -in @('Back', 'Exit')) { break }
            $sel = $r.Index
            if ($r.Index -lt $servers.Count) {
                if ($r.Key -eq 'RightArrow') {
                    if ((Show-ServerMenu $servers[$r.Index]) -eq 'exit') { break }
                } else {
                    Enter-Server $servers[$r.Index]
                }
                $online = $null
            } elseif ($r.Key -ne 'Enter') {
                continue
            } elseif ($r.Index -eq $iAdd) {
                Add-Server
                $online = $null
                $sel = 0
            } elseif ($r.Index -eq $iMng) {
                if ((Show-ManageMenu $online) -eq 'exit') { break }
                $online = $null
            } elseif ($r.Index -eq $iUpd) {
                if (Update-App) { return }
            } else {
                break
            }
        }
    } finally {
        Set-Cursor $true
    }
    [Console]::Write("$Esc[2J$Esc[H")
}

# ═══ запуск ══════════════════════════════════════════════════════════════════

function Start-Kvnbox([string[]]$CliArgs) {
    $self = $PSCommandPath
    $installed = $self -and ((Resolve-Path -LiteralPath $self).Path -eq [IO.Path]::GetFullPath($AppScript))
    if (-not $installed) {
        # первый запуск (irm | iex) или файл скачан вручную — установка и сразу приложение
        Write-Line
        Write-Line "$($Col.Cyan)kvnBox — установка приложения...$Rst"
        $text = if ($self) { [IO.File]::ReadAllText($self, [Text.Encoding]::UTF8) } else { Get-RemoteScript }
        $srcDir = if ($self) { Split-Path $self } else { '' }
        Install-App $text $srcDir
        Write-Ok 'Ярлык «kvnBox» — на рабочем столе и в меню Пуск.'
        Start-Sleep -Milliseconds 800
    }
    if ($CliArgs.Count -ge 2 -and $CliArgs[0] -eq '-Connect') {
        $s = @(Get-Servers | Where-Object { $_.Alias -eq $CliArgs[1] })
        if ($s.Count) { Enter-Server $s[0]; return }
    }
    Start-App
}

if (-not $env:KVNBOX_SETUP_TEST) { Start-Kvnbox $args }
