# kvnBox для Windows: веб-лаунчер — управление серверами kvnBox в браузере.
#
# Установка и обновление (PowerShell):
#   irm https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1 | iex
#
# Приложение ставится в %LOCALAPPDATA%\kvnBox, ярлык «kvnBox» — на рабочий стол и в меню Пуск.
# Запуск открывает страницу в браузере (127.0.0.1); PowerShell работает без окна (значок в трее) —
# связь страницы с серверами по SSH. Серверы: ключ SSH создаётся на ПК, добавляется на сервер (пароль root нужен один раз),
# сервер прописывается в ~\.ssh\config (вход `ssh kvnbox-<IP>` работает и из терминала).
# На чистый сервер kvnBox ставится прямо из лаунчера.

$KvnboxVersion = '3.0.10'

$ErrorActionPreference = 'Stop'
$RawUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ps1'
$IconUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/kvnbox.ico'
$WebUrl = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/windows/web.html'

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
$AppWeb = Join-Path $AppDir 'web.html'
$IperfDir = Join-Path $AppDir 'iperf3'
$IperfExe = Join-Path $IperfDir 'iperf3.exe'
# сборка iperf3 для Windows (github.com/ar51an/iperf3-win-builds), сверка по SHA-256
$IperfUrl = 'https://github.com/ar51an/iperf3-win-builds/releases/download/3.22/iperf-3.22-win64.zip'
$IperfSha = 'c9feabb3d721039508ccb81f74c2ada3ae11b9e75d33d24510573c2aa21da420'
$ServersFile = Join-Path $AppDir 'servers.json'
$UpdateFile = Join-Path $AppDir 'update.json'
$SshConfig = Join-Path $SshDir 'config'
$KnownHosts = [Environment]::GetEnvironmentVariable('KVNBOX_KNOWN_HOSTS')
$SshUser = Get-EnvOr 'KVNBOX_SSH_USER' 'root'   # другой пользователь — только для проверки

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
    $ErrorActionPreference = 'Continue'   # строки ssh в stderr — не исключение
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    $out = & $ssh @base -o BatchMode=yes -o "ConnectTimeout=$Timeout" $Alias $Command 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return (@($out) -join "`n")
}

function Invoke-SshProbe([string]$Alias, [string]$Command, [int]$Timeout = 10) {
    # как Invoke-Ssh, но с текстом ошибки ssh: отличить отказ ключа от обрыва сети
    $ErrorActionPreference = 'Continue'
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    $all = @(& $ssh @base -o BatchMode=yes -o "ConnectTimeout=$Timeout" $Alias $Command 2>&1)
    $code = $LASTEXITCODE
    $err = @($all | Where-Object { $_ -is [Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() }) -join "`n"
    $out = @($all | Where-Object { $_ -isnot [Management.Automation.ErrorRecord] }) -join "`n"
    return @{ Code = $code; Out = $out; Err = $err }
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
            "    User $SshUser",
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
    if (-not $ExtraArgs) {
        # главный ярлык: окна нет — работает в фоне, значок в трее, страница в браузере
        $h = Get-HiddenLaunch
        $lnk.TargetPath = $h.File
        $lnk.Arguments = $h.Args
        $lnk.WindowStyle = 7
    } elseif (Test-Path $wt) {
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

function Get-HiddenLaunch {
    # запуск без окна: conhost --headless (окно не создаётся, и Windows Terminal его не перехватывает)
    $ps = Get-PowerShellExe
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$AppScript`" -Bg"
    $conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
    if (Test-Path $conhost) { return @{ File = $conhost; Args = "--headless `"$ps`" $psArgs" } }
    return @{ File = $ps; Args = $psArgs }
}

function Start-Hidden {
    $h = Get-HiddenLaunch
    Start-Process $h.File -WindowStyle Hidden -ArgumentList $h.Args
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
    Install-WebPage $SourceDir
    New-Shortcut (Join-Path $DesktopDir 'kvnBox.lnk') 'kvnBox'
    New-Shortcut (Join-Path $StartMenuDir 'kvnBox.lnk') 'kvnBox'
    Import-OldServers
    Remove-OldShortcuts
    Update-ServerShortcuts
}

function Get-UpdateInfo([switch]$Force) {
    # раз в час спрашиваем GitHub (главное меню); без интернета — тихо
    $state = $null
    if (Test-Path $UpdateFile) {
        try { $state = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($UpdateFile)) } catch { }
    }
    $fresh = $state -and ([DateTime]::UtcNow - [DateTime]$state.Checked).TotalHours -lt 1
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

# ═══ окно PowerShell: только связь веб-меню с серверами ══════════════════════

$Esc = [char]27
$Rst = "$Esc[0m"
$Col = @{
    Blue = "$Esc[1;34m"; Green = "$Esc[1;32m"; Red = "$Esc[1;31m"; Yellow = "$Esc[1;33m"
    Cyan = "$Esc[1;36m"; Dim = "$Esc[2m"; Bold = "$Esc[1m"
}

function Write-Line([string]$t = '') { [Console]::WriteLine("  $t") }
function Write-Note([string]$t) { Write-Line "$($Col.Dim)$t$Rst" }
function Write-Ok([string]$t) { Write-Line "$($Col.Green)✓ $t$Rst" }
function Write-Fail([string]$t) { Write-Line "$($Col.Red)✗ $t$Rst" }

function Show-Error([string]$Text) {
    # без окна консоли сообщение видно только так
    Write-Fail $Text
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][Windows.Forms.MessageBox]::Show($Text, 'kvnBox', 'OK', 'Error')
    } catch { }
}

$RunFile = Join-Path $AppDir 'running.json'
$PrefsFile = Join-Path $AppDir 'prefs.json'

function Get-Prefs {
    # настройки страницы (тема, яркость, выбранный сервер): у страницы каждый раз новый порт,
    # а хранилище браузера привязано к адресу — поэтому храним на ПК, в папке лаунчера
    $h = @{}
    try {
        if (Test-Path $PrefsFile) {
            $o = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($PrefsFile, [Text.Encoding]::UTF8))
            foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = [string]$p.Value }
        }
    } catch { }
    return $h
}

function Get-PrefsScript {
    $json = ConvertTo-Json -InputObject (Get-Prefs) -Compress
    return '<script>window.KVN_PREFS=' + ($json -replace '<', '\u003c') + ';</script>'
}

function Open-RunningInstance {
    # kvnBox уже запущен — открыть его страницу и выйти (второй экземпляр не нужен)
    if (-not (Test-Path $RunFile)) { return $false }
    try {
        $st = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($RunFile))
        if (-not (Get-Process -Id ([int]$st.Pid) -ErrorAction SilentlyContinue)) { return $false }
        $req = [Net.WebRequest]::Create(($st.Url -replace '/\?t=', '/app/ping?t='))
        $req.Timeout = 2000
        $resp = $req.GetResponse(); $resp.Close()
        if (-not $env:KVNBOX_WEB_NOBROWSER) { Start-Process $st.Url }
        return $true
    } catch { return $false }
}

function Show-Header([string]$Title) {
    try { [Console]::Write("$Esc[2J$Esc[H") } catch { }
    Write-Line "$($Col.Blue)$('═' * 56)$Rst"
    Write-Line "$($Col.Yellow)kvnBox $KvnboxVersion — $Title$Rst"
    Write-Line "$($Col.Blue)$('─' * 56)$Rst"
    Write-Line
}

function Get-Online($Servers) {
    # доступность серверов: TCP до порта SSH, все сразу, не дольше ~2 с (медленные каналы)
    $res = @{}
    $probes = @()
    foreach ($s in @($Servers)) {
        $c = New-Object Net.Sockets.TcpClient
        $probes += @{ Alias = $s.Alias; Client = $c; Task = $c.ConnectAsync([string]$s.Host, [int]$s.Port) }
    }
    if ($probes.Count) {
        try { [Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($probes | ForEach-Object { $_.Task }), 2000) | Out-Null } catch { }
    }
    foreach ($p in $probes) {
        $res[$p.Alias] = ($p.Task.Status -eq 'RanToCompletion' -and $p.Client.Connected)
        $p.Client.Dispose()
    }
    return $res
}

# ═══ серверы: добавление, изменение, удаление (вызываются из веб-меню) ════════

function Update-Server($s) {
    $list = @(Get-Servers | ForEach-Object { if ($_.Alias -eq $s.Alias) { $s } else { $_ } })
    Save-Servers $list
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

function Get-AskPass {
    # пароль root для ssh без окна консоли: ssh спрашивает эту программу, она отдаёт
    # пароль из переменной окружения процесса ssh (на диск пароль не пишется)
    $path = Join-Path $AppDir 'askpass.cmd'
    $text = '@powershell -NoProfile -NonInteractive -Command "[Console]::Out.Write($env:KVNBOX_PW)"' + "`r`n"
    if (-not (Test-Path $path) -or [IO.File]::ReadAllText($path) -ne $text) {
        New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
        [IO.File]::WriteAllText($path, $text, [Text.Encoding]::ASCII)
    }
    return $path
}

function Invoke-SshPassword([string]$Server, [int]$Port, [string]$Password, [string]$Command, [string]$User = 'root', [string]$Stdin = '') {
    # вход по паролю (один раз, при добавлении сервера): @{ Code; Out; Err }.
    # $Stdin — данные для команды на сервере (пароли для sudo/su — не в командной строке)
    if (-not (Test-Path $SshConfig)) { Write-Utf8 $SshConfig '' }   # ssh -F не терпит отсутствия файла
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = Get-Tool 'ssh'
    $extra = if ($KnownHosts) { "-o `"UserKnownHostsFile=$KnownHosts`" " } else { '' }
    $psi.Arguments = "-F `"$SshConfig`" $extra-p $Port -o StrictHostKeyChecking=accept-new " +
        "-o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no " +
        "-o NumberOfPasswordPrompts=1 -o ConnectTimeout=15 $User@$Server `"$Command`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $psi.EnvironmentVariables['SSH_ASKPASS'] = Get-AskPass
    $psi.EnvironmentVariables['SSH_ASKPASS_REQUIRE'] = 'force'
    $psi.EnvironmentVariables['DISPLAY'] = 'localhost:0'
    $psi.EnvironmentVariables['KVNBOX_PW'] = $Password
    $p = [Diagnostics.Process]::Start($psi)
    if ($Stdin) {
        # байтами мимо StreamWriter (он ставит BOM перед первой строкой)
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Stdin)
        try { $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length); $p.StandardInput.BaseStream.Flush() } catch { }
    }
    # закрыть сам поток: StandardInput.Close() дописал бы BOM от StreamWriter
    try { $p.StandardInput.BaseStream.Close() } catch { }
    $outTask = $p.StandardOutput.ReadToEndAsync(); $errTask = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit(60000)) { try { $p.Kill() } catch { }; return @{ Code = -1; Out = ''; Err = 'нет ответа' } }
    return @{ Code = $p.ExitCode; Out = $outTask.Result; Err = $errTask.Result }
}

function Remove-KnownHost([string]$Server, [int]$Port) {
    # забыть сохранённый ключ сервера (сервер переустановлен — ключ сменился)
    $file = if ($KnownHosts) { $KnownHosts } else { Join-Path $SshDir 'known_hosts' }
    if (-not (Test-Path $file)) { return }
    $names = @($Server)
    if ($Port -ne 22) { $names += "[$Server]:$Port" }
    foreach ($n in $names) {
        $p = Start-Process -FilePath (Get-Tool 'ssh-keygen') -ArgumentList @('-R', "`"$n`"", '-f', "`"$file`"") -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput (Join-Path $AppDir 'keygen.out') -RedirectStandardError (Join-Path $AppDir 'keygen.err')
        [void]$p
    }
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $AppDir 'keygen.out'), (Join-Path $AppDir 'keygen.err')
}

function ConvertTo-B64([string]$Text) {
    return [Convert]::ToBase64String([Text.UTF8Encoding]::new($false).GetBytes($Text.Replace("`r`n", "`n")))
}

function Add-ServerWeb([string]$Server, [int]$Port, [string]$Password, [string]$Name, [bool]$ForgetHostKey = $false,
    [string]$User = 'root', [string]$RootPassword = '', [bool]$AllowRootKey = $false) {
    # Вход под $User по паролю → права root (сам root, sudo без пароля, sudo с тем же паролем,
    # su с паролем root) → ключ ПК пользователю и root → дальше лаунчер работает под root по ключу.
    # → @{ ok; error | alias, name, installed | hostkey_changed | need_root_password | need_root_key }
    $Server = $Server.Trim()
    if (-not ($Server -match '^[A-Za-z0-9.:-]+$')) { return @{ ok = $false; error = 'Неверный адрес сервера.' } }
    if ($Port -lt 1 -or $Port -gt 65535) { return @{ ok = $false; error = 'Порт SSH: 1–65535.' } }
    if (-not ($User -match '^[a-z_][a-z0-9_.-]{0,31}$')) { return @{ ok = $false; error = 'Неверное имя пользователя.' } }
    if (-not $Password) { return @{ ok = $false; error = 'Введите пароль.' } }
    $alias = Get-Alias $Server
    $key = Join-Path $SshDir $alias
    try { New-ServerKey $key } catch { return @{ ok = $false; error = $_.Exception.Message } }
    $pub = ([IO.File]::ReadAllText("$key.pub")).Trim()
    # от root: ключ ПК — root, kvnBox ли на сервере, правило входа root; по согласию — вход root только по ключу
    $allow = if ($AllowRootKey) { '1' } else { '0' }
    $rootScript = @"
umask 077; mkdir -p /root/.ssh; touch /root/.ssh/authorized_keys
grep -qxF '$pub' /root/.ssh/authorized_keys || echo '$pub' >> /root/.ssh/authorized_keys
command -v kvnbox >/dev/null && echo KVNBOX_INSTALLED
if [ $allow = 1 ]; then
  mkdir -p /etc/ssh/sshd_config.d
  printf '# kvnBox: root login by key only\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/00-kvnbox-rootkey.conf
  chmod 644 /etc/ssh/sshd_config.d/00-kvnbox-rootkey.conf
  (systemctl reload ssh || systemctl reload sshd) >/dev/null 2>&1
fi
echo KVNBOX_PRL=`$(sshd -T 2>/dev/null | awk '/^permitrootlogin/{print `$2}')
echo KVNBOX_ROOT_OK
"@
    # от пользователя: ключ ПК себе, затем права root любым доступным способом (пароли — из stdin)
    $userScript = @"
IFS= read -r PW; IFS= read -r RPW; PW=`${PW#`$'\xef\xbb\xbf'}
umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys
grep -qxF '$pub' ~/.ssh/authorized_keys || echo '$pub' >> ~/.ssh/authorized_keys
echo KVNBOX_FQDN=`$(hostname -f 2>/dev/null || hostname)
R=`$(mktemp); echo '$(ConvertTo-B64 $rootScript)' | base64 -d > "`$R"; chmod 644 "`$R"
if [ "`$(id -u)" = 0 ]; then echo KVNBOX_MODE=root; bash "`$R"
elif command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then echo KVNBOX_MODE=sudo; sudo -n bash "`$R"
elif command -v sudo >/dev/null 2>&1 && printf '%s\n' "`$PW" | sudo -S -p '' true 2>/dev/null; then echo KVNBOX_MODE=sudo; printf '%s\n' "`$PW" | sudo -S -p '' bash "`$R"
elif [ -n "`$RPW" ]; then echo KVNBOX_MODE=su; { sleep 1; printf '%s\n' "`$RPW"; } | script -qec "su -c 'bash `$R'" /dev/null
else echo KVNBOX_MODE=none; command -v su >/dev/null && echo KVNBOX_HAS_SU; fi
rm -f "`$R"
"@
    $cmd = 'f=$(mktemp) && echo ' + (ConvertTo-B64 $userScript) + ' | base64 -d > $f && bash $f; rc=$?; rm -f $f; exit $rc'
    if ($ForgetHostKey) { Remove-KnownHost $Server $Port }
    $r = Invoke-SshPassword $Server $Port $Password $cmd $User ("$Password`n$RootPassword`n")
    if ($r.Code -ne 0 -and $r.Err -match 'Host key verification failed|IDENTIFICATION HAS CHANGED') {
        # ключ сервера не совпал с сохранённым: решает пользователь (переустановка или подмена)
        return @{ ok = $true; hostkey_changed = $true }
    }
    if ($r.Out -notmatch 'KVNBOX_FQDN=') {
        $why = if ($r.Err -match 'Permission denied') { "неверный пароль или вход пользователя $User по паролю запрещён" }
        elseif ($r.Err -match 'timed out|нет ответа') { 'сервер не отвечает (проверьте IP и порт)' }
        elseif ($r.Err -match 'refused') { 'порт SSH закрыт' }
        else { (($r.Err -split "`n" | Where-Object { $_.Trim() -and $_ -notmatch '^Warning' }) | Select-Object -Last 1) }
        return @{ ok = $false; error = "Не удалось подключиться: $why" }
    }
    $mode = if ($r.Out -match 'KVNBOX_MODE=(\w+)') { $Matches[1] } else { '' }
    if ($r.Out -notmatch 'KVNBOX_ROOT_OK') {
        if ($mode -eq 'none' -and $r.Out -match 'KVNBOX_HAS_SU') { return @{ ok = $true; need_root_password = $true } }
        if ($mode -eq 'su') { return @{ ok = $false; error = 'Пароль root не подошёл.' } }
        return @{ ok = $false; error = "Вход под $User есть, но прав администратора нет: нужен root или пользователь с sudo." }
    }
    $prl = if ($r.Out -match 'KVNBOX_PRL=(\S+)') { $Matches[1] } else { '' }
    if ($prl -in @('no', 'forced-commands-only') -and -not $AllowRootKey) {
        # сервер не пускает root даже по ключу, а kvnBox работает от root: спросить
        return @{ ok = $true; need_root_key = $true }
    }
    Set-SshConfigHost $alias $Server $Port $key
    $okKey = $false
    foreach ($try in 1..3) { if ($null -ne (Invoke-Ssh $alias 'true' 12)) { $okKey = $true; break }; Start-Sleep -Seconds 2 }
    if (-not $okKey) {
        return @{ ok = $false; error = 'Ключ добавлен на сервер, но вход root по нему не прошёл.' }
    }
    $fqdn = ''
    foreach ($line in ($r.Out -split "`n")) { if ($line -match '^KVNBOX_FQDN=(\S+)') { $fqdn = $Matches[1] } }
    if (-not $fqdn -or $fqdn -eq 'localhost') { $fqdn = $Server }
    if (-not $Name) { $Name = $fqdn }
    $list = @(Get-Servers | Where-Object { $_.Alias -ne $alias })
    $list += [pscustomobject]@{ Name = $Name; Host = $Server; Port = $Port; Alias = $alias; Key = $key }
    Save-Servers $list
    return @{ ok = $true; alias = $alias; name = $Name; installed = ($r.Out -match 'KVNBOX_INSTALLED'); via = $mode }
}

function Edit-ServerWeb([string]$Alias, [string]$Name, [int]$Port) {
    $s = @(Get-Servers | Where-Object { $_.Alias -eq $Alias }) | Select-Object -First 1
    if (-not $s) { return @{ ok = $false; error = 'Сервер не найден.' } }
    if ($Port -lt 1 -or $Port -gt 65535) { return @{ ok = $false; error = 'Порт SSH: 1–65535.' } }
    if ($Name.Trim()) { $s.Name = $Name.Trim() }
    if ($Port -ne [int]$s.Port) {
        $s.Port = $Port
        Set-SshConfigHost $s.Alias $s.Host $s.Port $s.Key
    }
    Update-Server $s
    return @{ ok = $true }
}

function Get-ServerState($s) {
    # @{ Password = $true/$false; Keys = N } или $null
    # без kvnbox: работает и на чистом сервере
    $cmd = 'echo PW=$(sshd -T 2>/dev/null | awk ''/^passwordauthentication/{print $2}''); ' +
        'echo KEYS=$(grep -cE ''^(ssh-|ecdsa-|sk-)'' ~/.ssh/authorized_keys 2>/dev/null)'
    $text = Invoke-Ssh $s.Alias $cmd
    if ($null -eq $text) { return $null }
    $pw = $null
    if ($text -match 'PW=(yes|no)') { $pw = ($Matches[1] -eq 'yes') }
    $keys = 0
    if ($text -match 'KEYS=(\d+)') { $keys = [int]$Matches[1] }
    return @{ Password = $pw; Keys = $keys }
}

function Remove-ServerWeb([string]$Alias) {
    # ключ этого ПК убирается с сервера, если это не единственный способ входа
    $s = @(Get-Servers | Where-Object { $_.Alias -eq $Alias }) | Select-Object -First 1
    if (-not $s) { return @{ ok = $false; error = 'Сервер не найден.' } }
    $note = ''
    $state = Get-ServerState $s
    if ($null -eq $state) { Start-Sleep -Seconds 1; $state = Get-ServerState $s }   # повтор: сеть бывает нестабильна
    $pub = $null
    if (Test-Path "$($s.Key).pub") { $pub = ([IO.File]::ReadAllText("$($s.Key).pub")).Trim() }
    if ($null -eq $state) {
        $note = 'Сервер не ответил — ключ этого компьютера на нём остался.'
    } elseif ($state.Password -eq $false -and $state.Keys -le 1) {
        $note = 'Ключ на сервере оставлен: он единственный, а вход по паролю отключён.'
    } elseif ($pub) {
        $body = ($pub -split '\s+')[1]
        $cmd = 'f=~/.ssh/authorized_keys; grep -vF ''' + $body + ''' "$f" > "$f.kvnbox"; ' +
            'cat "$f.kvnbox" > "$f"; rm -f "$f.kvnbox"'
        if ($null -ne (Invoke-Ssh $s.Alias $cmd)) { $note = 'Ключ этого компьютера удалён с сервера.' }
        else { $note = 'Не удалось удалить ключ с сервера.' }
    }
    Clear-SshConfigHost $s.Alias
    foreach ($f in @($s.Key, "$($s.Key).pub")) {
        if ($f -and (Test-Path -LiteralPath $f)) { Remove-Item -LiteralPath $f -Force }
    }
    $lnk = Get-ServerShortcut $s
    if (Test-Path -LiteralPath $lnk) { Remove-Item -LiteralPath $lnk -Force }
    Save-Servers @(Get-Servers | Where-Object { $_.Alias -ne $s.Alias })
    return @{ ok = $true; note = $note }
}

function Open-ServerTerminal($s, [string]$Mode = 'shell') {
    # окно терминала: командная строка сервера или меню kvnBox
    $ssh = Get-Tool 'ssh'
    $tail = if ($Mode -eq 'menu') { ' menu' } else { '' }
    $cmd = "& '$ssh' -F '$SshConfig' -t $($s.Alias)$tail"
    $wt = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\wt.exe'
    if (Test-Path $wt) {
        Start-Process $wt -ArgumentList @('--title', "`"kvnBox - $($s.Name)`"", (Get-PowerShellExe), '-NoProfile', '-Command', $cmd)
    } else {
        Start-Process (Get-PowerShellExe) -ArgumentList @('-NoProfile', '-Command', $cmd)
    }
}

function Enter-Server($s) {
    # ярлыки серверов прежних версий (-Connect): меню сервера в этом окне
    $Host.UI.RawUI.WindowTitle = "kvnBox - $($s.Name)"
    $ssh = Get-Tool 'ssh'
    $base = Get-SshBase
    & $ssh @base -t $s.Alias menu
}

function Start-InstallJob($s, [string]$Domain) {
    # установка kvnBox на чистый сервер: установщик с GitHub, домен — переменной,
    # вывод построчно (без терминала установщик пишет «[ok] этап»). Установщик работает на
    # сервере в фоне (nohup): обрыв SSH его не прерывает; сюда идёт `tail` его вывода.
    $url = 'https://raw.githubusercontent.com/denash-git/kvnBox/main/install.sh'
    $remote = 'command -v curl >/dev/null || (apt-get update -q && apt-get install -y -q curl) >/dev/null 2>&1; ' +
        "curl -fsSL --retry 3 '$url`?t=$([DateTime]::UtcNow.Ticks)' -o ~/kvnbox-install.sh || { echo 'Не удалось скачать установщик с GitHub.'; exit 2; }; " +
        "grep -q KVNBOX_DOMAIN ~/kvnbox-install.sh || { echo 'Установщик на GitHub старой версии: он не умеет ставиться из лаунчера. Поставьте вручную: bash <(wget -qO- $url)'; exit 3; }; " +
        "pgrep -fx 'bash .*/kvnbox-install.sh' >/dev/null && { echo 'На сервере уже идёт установка kvnBox.'; exit 4; }; " +
        "KVNBOX_DOMAIN='$Domain' nohup bash ~/kvnbox-install.sh > ~/kvnbox-install.out 2>&1 < /dev/null & " +
        'P=$!; tail -n +1 -f --pid=$P ~/kvnbox-install.out; wait $P'
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = Get-Tool 'ssh'
    $extra = if ($KnownHosts) { "-o `"UserKnownHostsFile=$KnownHosts`" " } else { '' }
    $psi.Arguments = "-F `"$SshConfig`" $extra-o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=20 $($s.Alias) `"$remote`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $p = [Diagnostics.Process]::Start($psi)
    $p.StandardInput.Close()
    return $p
}

function Update-AppWeb {
    # новая версия лаунчера с GitHub; '' — готово, иначе текст ошибки
    try {
        $text = Get-RemoteScript
        Install-App $text
        return ''
    } catch { return "Обновить не удалось: $($_.Exception.Message)" }
}

# ═══ веб-меню ════════════════════════════════════════════════════════════════
# Страница в браузере ↔ этот PowerShell (127.0.0.1, одноразовый ключ) ↔ один SSH-канал на сервер
# (`kvnbox api serve`). На сервере ничего не слушает. Строки долгих операций и журналов — в
# браузер потоком (EventSource). Всё в одном потоке: ожидание сразу HTTP и ответов серверов.

function Install-WebPage([string]$SourceDir = '') {
    try {
        $local = if ($SourceDir) { Join-Path $SourceDir 'web.html' } else { '' }
        if ($local -and (Test-Path $local) -and ([IO.Path]::GetFullPath($local) -ne [IO.Path]::GetFullPath($AppWeb))) {
            $text = [IO.File]::ReadAllText($local, [Text.Encoding]::UTF8)
        } else {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $wc = New-Object Net.WebClient
            $wc.Encoding = [Text.Encoding]::UTF8
            $text = $wc.DownloadString("$WebUrl`?t=" + [DateTime]::UtcNow.Ticks)
        }
        if ($text -match '<!doctype html>' -and $text -match 'kvnBox' -and $text -match '</html>') {
            Write-Utf8 $AppWeb $text
        }
    } catch { }
}

function ConvertTo-AsciiJson([string]$s) {
    # в stdin ssh — только ASCII: кодировка консоли не важна
    return [regex]::Replace($s, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
}

function Open-ApiSession([string]$Alias) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = Get-Tool 'ssh'
    $extra = if ($KnownHosts) { "-o `"UserKnownHostsFile=$KnownHosts`" " } else { '' }
    $psi.Arguments = "-F `"$SshConfig`" -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=20 " +
        "$extra$Alias kvnbox api serve"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $psi.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $p = [Diagnostics.Process]::Start($psi)
    return @{
        Alias = $Alias; Proc = $p; Pending = @{}; Ready = $false
        Queue = New-Object System.Collections.Generic.List[string]   # до приветствия сервера
        Read = $p.StandardOutput.ReadLineAsync(); Err = $p.StandardError.ReadToEndAsync()
    }
}

function Write-ApiLine($Sess, [string]$Line) {
    # байтами ASCII мимо StreamWriter: он ставит BOM перед первой строкой, и сервер её не разбирает
    $bytes = [Text.Encoding]::ASCII.GetBytes($Line)
    $Sess.Proc.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
    $Sess.Proc.StandardInput.BaseStream.Flush()
}

function Close-ApiSession($Sess, [string]$Why) {
    try { $Sess.Proc.StandardInput.Close() } catch { }
    try { if (-not $Sess.Proc.WaitForExit(1500)) { $Sess.Proc.Kill() } } catch { }
    $err = ''
    try { if ($Sess.Err.Wait(500)) { $err = ($Sess.Err.Result -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 2) -join ' ' } } catch { }
    foreach ($id in @($Sess.Pending.Keys)) {
        $text = "Соединение с сервером потеряно. $Why $err".Trim()
        $json = '{"id":"' + $id + '","ok":false,"error":' + (ConvertTo-Json $text -Compress) + '}'
        Send-WebResponse $Sess.Pending[$id] 200 'application/json; charset=utf-8' $json
    }
    $Sess.Pending.Clear()
}

function Send-WebResponse($Ctx, [int]$Code, [string]$Type, [string]$Text) {
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        $Ctx.Response.StatusCode = $Code
        $Ctx.Response.ContentType = $Type
        $Ctx.Response.Headers['Cache-Control'] = 'no-store'
        $Ctx.Response.Headers['X-Content-Type-Options'] = 'nosniff'
        $Ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $Ctx.Response.Close()
    } catch { }
}

function Send-WebEvent($Streams, [string]$Data) {
    $bytes = [Text.Encoding]::UTF8.GetBytes("data: $Data`n`n")
    foreach ($st in $Streams.ToArray()) {
        try { $st.Response.OutputStream.Write($bytes, 0, $bytes.Length); $st.Response.OutputStream.Flush() }
        catch { [void]$Streams.Remove($st) }
    }
}

function Install-Iperf {
    # iperf3 для замера скорости ПК ↔ сервер: zip из релиза, сверка SHA-256, распаковка в папку лаунчера.
    # Возвращает текст ошибки или пустую строку.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("kvnbox-iperf-" + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        $zip = Join-Path $tmp 'iperf3.zip'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        (New-Object Net.WebClient).DownloadFile($IperfUrl, $zip)
        $hash = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLower()
        if ($hash -ne $IperfSha) { return 'Скачанный iperf3 не совпал с ожидаемой контрольной суммой — установка отменена.' }
        $unz = Join-Path $tmp 'x'
        Expand-Archive -Path $zip -DestinationPath $unz -Force
        $exe = Get-ChildItem -Path $unz -Recurse -Filter 'iperf3.exe' | Select-Object -First 1
        if (-not $exe) { return 'В архиве нет iperf3.exe.' }
        New-Item -ItemType Directory -Force -Path $IperfDir | Out-Null
        Copy-Item -Path (Join-Path $exe.DirectoryName '*') -Destination $IperfDir -Force
        return ''
    } catch {
        return "Не удалось скачать iperf3: $($_.Exception.Message)"
    } finally {
        Remove-Item -Recurse -Force -Path $tmp -ErrorAction SilentlyContinue
    }
}

function Start-WebMenu {
    if (-not (Test-Path $AppWeb)) { Install-WebPage $PSScriptRoot }
    if (-not (Test-Path $AppWeb)) {
        Show-Error 'Страница kvnBox не найдена и не скачалась. Проверьте интернет и повторите.'
        return
    }
    $page = [IO.File]::ReadAllText($AppWeb, [Text.Encoding]::UTF8)
    $token = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $listener = $null
    foreach ($try in 1..30) {
        $port = Get-Random -Minimum 47000 -Maximum 48999
        $l = New-Object Net.HttpListener
        $l.Prefixes.Add("http://127.0.0.1:$port/")
        try { $l.Start(); $listener = $l; break } catch { $l.Close() }
    }
    if (-not $listener) { Show-Error 'Не удалось запустить веб-сервер на ПК.'; return }
    $url = "http://127.0.0.1:$port/?t=$token"
    $hostOk = "127.0.0.1:$port"

    $Host.UI.RawUI.WindowTitle = 'kvnBox'
    Show-Header 'web лаунчер'
    Write-Line 'kvnBox открыт в браузере. Это окно — его связь с серверами:'
    Write-Line 'пока оно открыто, страница работает. Окно можно свернуть.'
    Write-Line
    Write-Line "Адрес: $($Col.Cyan)$url$Rst"
    Write-Note 'Адрес одноразовый: после закрытия окна он перестаёт работать.'
    Write-Line
    Write-Line "$($Col.Yellow)q$Rst — закрыть kvnBox"
    Write-Line
    if (-not $env:KVNBOX_WEB_NOBROWSER) { Start-Process $url }
    try { Write-Utf8 $RunFile (ConvertTo-Json -InputObject @{ Pid = $PID; Url = $url } -Compress) } catch { }

    # значок в трее: открыть страницу / закрыть kvnBox
    $script:TrayQuit = $false
    $script:TrayUrl = $url
    $tray = $null
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $tray = New-Object Windows.Forms.NotifyIcon
        $tray.Icon = if (Test-Path $AppIcon) { New-Object Drawing.Icon $AppIcon } else { [Drawing.SystemIcons]::Application }
        $tray.Text = "kvnBox $KvnboxVersion"
        $menu = New-Object Windows.Forms.ContextMenuStrip
        [void]$menu.Items.Add('Открыть kvnBox', $null, { Start-Process $script:TrayUrl })
        [void]$menu.Items.Add('Закрыть kvnBox', $null, { $script:TrayQuit = $true })
        $tray.ContextMenuStrip = $menu
        $tray.add_MouseClick({ param($s, $e) if ($e.Button -eq [Windows.Forms.MouseButtons]::Left) { Start-Process $script:TrayUrl } })
        $tray.Visible = $true
        $tray.ShowBalloonTip(4000, 'kvnBox запущен', 'Страница открыта в браузере. Значок kvnBox здесь: открыть или закрыть.', 'Info')
    } catch {
        $tray = $null
        try { [IO.File]::AppendAllText((Join-Path $AppDir 'web.log'), "$([DateTime]::Now.ToString('s')) tray: $($_.Exception.Message)`n") } catch { }
    }
    $idleSince = [DateTime]::UtcNow   # нет открытых вкладок — с этого момента
    $idleMinutes = [double](Get-EnvOr 'KVNBOX_IDLE_MINUTES' '10')   # KVNBOX_* — для проверки

    $sessions = @{}
    $streams = New-Object System.Collections.Generic.List[object]
    $jobs = New-Object System.Collections.Generic.List[object]   # замеры iperf3 с ПК
    $streamJobs = New-Object System.Collections.Generic.List[object]   # установка kvnBox: вывод построчно
    $quit = $false; $restart = $false
    $latest = Get-UpdateInfo
    Update-DefaultNames (Get-Online @(Get-Servers))
    $httpTask = $listener.GetContextAsync()
    $lastPing = [DateTime]::UtcNow
    $webLog = Join-Path $AppDir 'web.log'
    try {
        while ($true) {
          try {
            try { if ([Console]::KeyAvailable) { $k = [Console]::ReadKey($true); if ($k.Key -in @('Q', 'Escape', 'D0')) { break } } } catch { }
            $tasks = New-Object System.Collections.Generic.List[Threading.Tasks.Task]
            $tasks.Add($httpTask)
            foreach ($s in $sessions.Values) { $tasks.Add($s.Read) }
            foreach ($j in $jobs) { $tasks.Add($j.Out) }
            foreach ($j in $streamJobs) { $tasks.Add($j.Read) }
            [void][Threading.Tasks.Task]::WaitAny($tasks.ToArray(), 250)
            if ($tray) { [Windows.Forms.Application]::DoEvents() }
            if ($script:TrayQuit) { $quit = $true }
            if ($quit) { Start-Sleep -Milliseconds 300; break }
            # все вкладки закрыты 10 минут и ничего не выполняется — выключиться
            if ($streams.Count -gt 0 -or $jobs.Count -gt 0 -or $streamJobs.Count -gt 0) { $idleSince = [DateTime]::UtcNow }
            elseif (([DateTime]::UtcNow - $idleSince).TotalMinutes -ge $idleMinutes) { break }

            # установка kvnBox: строки — в браузер, в конце — ответ на запрос
            foreach ($j in $streamJobs.ToArray()) {
                while ($j.Read.IsCompleted) {
                    $line = $null
                    try { $line = $j.Read.Result } catch { }
                    if ($null -eq $line) {
                        [void]$streamJobs.Remove($j)
                        try { [void]$j.Proc.WaitForExit(3000) } catch { }
                        $code = try { $j.Proc.ExitCode } catch { -1 }
                        $json = if ($code -eq 0) { '{"ok":true,"data":{"code":0}}' }
                        else { '{"ok":false,"error":' + (ConvertTo-Json "Установка не завершена (код $code): причина — в выводе выше; журнал на сервере — /var/log/kvnbox-install.log." -Compress) + '}' }
                        Send-WebResponse $j.Ctx 200 'application/json; charset=utf-8' $json
                        break
                    }
                    $j.Read = $j.Proc.StandardOutput.ReadLineAsync()
                    $clean = ($line -replace "$([char]27)\[[0-9;?]*[A-Za-z]", '').TrimEnd()
                    if ($clean) { Send-WebEvent $streams ('{"s":"","m":{"id":' + (ConvertTo-Json $j.Id -Compress) + ',"event":"line","data":' + (ConvertTo-Json $clean -Compress) + '}}') }
                }
            }

            # замеры iperf3 завершились
            foreach ($j in $jobs.ToArray()) {
                if (-not $j.Out.IsCompleted) { continue }
                [void]$jobs.Remove($j)
                $out = ''
                try { $out = $j.Out.Result.Trim() } catch { }
                try { [void]$j.Proc.WaitForExit(2000) } catch { }
                if ($out.StartsWith('{')) { $json = '{"ok":true,"data":' + $out + '}' }
                else { $json = '{"ok":false,"error":' + (ConvertTo-Json ("iperf3: " + $out) -Compress) + '}' }
                Send-WebResponse $j.Ctx 200 'application/json; charset=utf-8' $json
            }

            # ответы серверов
            foreach ($alias in @($sessions.Keys)) {
                $s = $sessions[$alias]
                while ($s.Read.IsCompleted) {
                    $line = $null
                    try { $line = $s.Read.Result } catch { }
                    if ($null -eq $line) { Close-ApiSession $s ''; $sessions.Remove($alias); break }
                    $s.Read = $s.Proc.StandardOutput.ReadLineAsync()
                    if ($env:KVNBOX_WEB_DEBUG) { Add-Content -Path $webLog -Value ("< " + $line.Substring(0, [Math]::Min(90, $line.Length))) -Encoding UTF8 }
                    if (-not $s.Ready -and $line -match '"ready": true') {
                        # ssh для Windows теряет ввод, отправленный до открытия канала: запросы ждали приветствия
                        $s.Ready = $true
                        try { foreach ($q in $s.Queue) { Write-ApiLine $s $q } } catch { }
                        $s.Queue.Clear()
                    } elseif ($line -match '^\{"id": "([^"]*)", "event"') {
                        Send-WebEvent $streams ('{"s":' + (ConvertTo-Json $alias -Compress) + ',"m":' + $line + '}')
                    } elseif ($line -match '^\{"id": "([^"]+)", "ok"') {
                        $id = $Matches[1]
                        if ($s.Pending.ContainsKey($id)) {
                            Send-WebResponse $s.Pending[$id] 200 'application/json; charset=utf-8' $line
                            $s.Pending.Remove($id)
                        }
                    }
                }
            }

            # поддержка потока событий (прокси и браузер не рвут молчащее соединение)
            if (([DateTime]::UtcNow - $lastPing).TotalSeconds -ge 15) {
                $lastPing = [DateTime]::UtcNow
                $bytes = [Text.Encoding]::UTF8.GetBytes(": ping`n`n")
                foreach ($st in $streams.ToArray()) { try { $st.Response.OutputStream.Write($bytes, 0, $bytes.Length); $st.Response.OutputStream.Flush() } catch { [void]$streams.Remove($st) } }
            }

            if (-not $httpTask.IsCompleted) { continue }
            $ctx = $null
            try { $ctx = $httpTask.Result } catch { }
            $httpTask = $listener.GetContextAsync()
            if (-not $ctx) { continue }
            $req = $ctx.Request
            $path = $req.Url.AbsolutePath
            if ($req.Headers['Host'] -ne $hostOk -or $req.QueryString['t'] -ne $token) {
                Send-WebResponse $ctx 403 'text/plain; charset=utf-8' 'Откройте адрес из окна лаунчера kvnBox.'
                continue
            }
            if ($path -eq '/app/ping') {
                Send-WebResponse $ctx 200 'text/plain' 'ok'
            } elseif ($path -eq '/' -and $req.HttpMethod -eq 'GET') {
                Send-WebResponse $ctx 200 'text/html; charset=utf-8' ($page.Replace('<head>', '<head>' + (Get-PrefsScript)))
            } elseif ($path -eq '/app/prefs' -and $req.HttpMethod -eq 'POST') {
                # {ключ: значение | null} — дописать в prefs.json
                try {
                    $in = ConvertFrom-Json -InputObject ((New-Object IO.StreamReader($req.InputStream, [Text.Encoding]::UTF8)).ReadToEnd())
                    $h = Get-Prefs
                    foreach ($p in $in.PSObject.Properties) {
                        if ($p.Name -notmatch '^[\w-]{1,40}$') { continue }
                        if ($null -eq $p.Value) { $h.Remove($p.Name) } else { $h[$p.Name] = ([string]$p.Value).Substring(0, [Math]::Min(200, ([string]$p.Value).Length)) }
                    }
                    Write-Utf8 $PrefsFile (ConvertTo-Json -InputObject $h -Compress)
                    Send-WebResponse $ctx 200 'application/json' '{"ok":true}'
                } catch { Send-WebResponse $ctx 400 'application/json' '{"ok":false}' }
            } elseif ($path -eq '/app/servers') {
                $list = @(Get-Servers)
                $on = Get-Online $list
                $srv = @($list | ForEach-Object { [pscustomobject]@{ Alias = $_.Alias; Name = $_.Name; Host = $_.Host; Port = $_.Port; Online = [bool]$on[$_.Alias] } })
                $latest = if ($req.QueryString['force'] -eq '1') { Get-UpdateInfo -Force } else { Get-UpdateInfo }
                $upd = if (Test-Newer $latest) { $latest } else { '' }
                $json = ConvertTo-Json -InputObject @{ launcher = $KvnboxVersion; update = $upd; servers = $srv } -Depth 4 -Compress
                Send-WebResponse $ctx 200 'application/json; charset=utf-8' $json
            } elseif ($path -like '/app/server_*' -or $path -in @('/app/self_update', '/app/quit', '/app/terminal')) {
                # главное меню лаунчера: тело — JSON
                $in = $null
                try { $in = ConvertFrom-Json -InputObject ((New-Object IO.StreamReader($req.InputStream, [Text.Encoding]::UTF8)).ReadToEnd()) } catch { }
                if ($req.HttpMethod -ne 'POST' -or -not $in) { Send-WebResponse $ctx 400 'application/json' '{"ok":false,"error":"bad request"}'; continue }
                $srvObj = $null
                if ($in.alias) { $srvObj = @(Get-Servers) | Where-Object { $_.Alias -eq [string]$in.alias } | Select-Object -First 1 }
                $res = $null
                try {
                switch ($path) {
                    '/app/server_add' {
                        $u = if ([string]$in.user) { ([string]$in.user).Trim() } else { 'root' }
                        $res = Add-ServerWeb ([string]$in.host) ([int]$in.port) ([string]$in.password) ([string]$in.name) ([bool]$in.forget_hostkey) $u ([string]$in.root_password) ([bool]$in.allow_root_key)
                    }
                    '/app/server_edit' { $res = Edit-ServerWeb ([string]$in.alias) ([string]$in.name) ([int]$in.port) }
                    '/app/server_delete' {
                        if ($sessions.ContainsKey([string]$in.alias)) { Close-ApiSession $sessions[[string]$in.alias] 'Сервер удалён.'; $sessions.Remove([string]$in.alias) }
                        $res = Remove-ServerWeb ([string]$in.alias)
                    }
                    '/app/server_check' {
                        if (-not $srvObj) { $res = @{ ok = $false; error = 'Сервер не найден.' }; break }
                        # до трёх попыток: на нестабильном канале новые SSH-подключения иногда обрываются
                        $r = $null
                        foreach ($try in 1..3) {
                            $r = Invoke-SshProbe $srvObj.Alias 'command -v kvnbox >/dev/null && kvnbox --version 2>/dev/null | head -1 || echo KVNBOX_NONE' 10
                            if ($r.Code -eq 0 -or $r.Err -match 'Permission denied') { break }
                            Start-Sleep -Milliseconds 800
                        }
                        if ($r.Code -ne 0) {
                            $kind = if ($r.Err -match 'Permission denied') { 'auth' } else { 'net' }
                            $res = @{ ok = $true; reachable = $false; kind = $kind; detail = (($r.Err -split "`n" | Where-Object { $_.Trim() }) | Select-Object -Last 1) }
                        }
                        elseif ($r.Out -match 'KVNBOX_NONE') { $res = @{ ok = $true; reachable = $true; installed = $false } }
                        else { $res = @{ ok = $true; reachable = $true; installed = $true; version = ($r.Out.Trim() -replace '^\D*', '') } }
                    }
                    '/app/server_install' {
                        $dom = ([string]$in.domain).Trim().ToLower()
                        if (-not $srvObj) { $res = @{ ok = $false; error = 'Сервер не найден.' }; break }
                        if ($dom -notmatch '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$') { $res = @{ ok = $false; error = 'Неверный домен.' }; break }
                        if ([string]$in.id -notmatch '^[A-Za-z0-9-]+$') { $res = @{ ok = $false; error = 'bad id' }; break }
                        try {
                            $p = Start-InstallJob $srvObj $dom
                            $streamJobs.Add(@{ Ctx = $ctx; Proc = $p; Id = [string]$in.id; Alias = $srvObj.Alias; Read = $p.StandardOutput.ReadLineAsync() })
                        } catch { $res = @{ ok = $false; error = "Не удалось запустить ssh: $($_.Exception.Message)" } }
                    }
                    '/app/server_install_abort' {
                        if (-not $srvObj) { $res = @{ ok = $false; error = 'Сервер не найден.' }; break }
                        [void](Invoke-Ssh $srvObj.Alias "pkill -TERM -fx 'bash .*/kvnbox-install.sh'; sleep 1; pgrep -fx 'bash .*/kvnbox-install.sh' >/dev/null && pkill -KILL -fx 'bash .*/kvnbox-install.sh'; true" 15)
                        foreach ($j in $streamJobs.ToArray()) { if ($j.Alias -eq $srvObj.Alias) { try { $j.Proc.Kill() } catch { } } }
                        $res = @{ ok = $true }
                    }
                    '/app/terminal' {
                        if ($srvObj) { Open-ServerTerminal $srvObj ([string]$in.mode); $res = @{ ok = $true } } else { $res = @{ ok = $false; error = 'Сервер не найден.' } }
                    }
                    '/app/self_update' {
                        $err = Update-AppWeb
                        if ($err) { $res = @{ ok = $false; error = $err } } else { $res = @{ ok = $true }; $quit = $true; $restart = $true }
                    }
                    '/app/quit' { $res = @{ ok = $true }; $quit = $true }
                }
                } catch { $res = @{ ok = $false; error = "Ошибка лаунчера: $($_.Exception.Message)" } }
                if ($null -ne $res) {
                    $ok = [bool]$res.ok
                    $data = @{}; foreach ($k in $res.Keys) { if ($k -notin @('ok', 'error')) { $data[$k] = $res[$k] } }
                    $json = if ($ok) { ConvertTo-Json -InputObject @{ ok = $true; data = $data } -Depth 4 -Compress }
                    else { ConvertTo-Json -InputObject @{ ok = $false; error = [string]$res.error } -Compress }
                    Send-WebResponse $ctx 200 'application/json; charset=utf-8' $json
                }
            } elseif ($path -eq '/app/events') {
                $ctx.Response.ContentType = 'text/event-stream; charset=utf-8'
                $ctx.Response.Headers['Cache-Control'] = 'no-store'
                $ctx.Response.SendChunked = $true
                $b = [Text.Encoding]::UTF8.GetBytes(": ok`n`n")
                try { $ctx.Response.OutputStream.Write($b, 0, $b.Length); $ctx.Response.OutputStream.Flush(); $streams.Add($ctx) } catch { }
            } elseif ($path -eq '/app/call' -and $req.HttpMethod -eq 'POST') {
                $alias = [string]$req.QueryString['s']
                $body = (New-Object IO.StreamReader($req.InputStream, [Text.Encoding]::UTF8)).ReadToEnd()
                if (-not ($body -match '^\s*\{\s*"id"\s*:\s*"([A-Za-z0-9-]+)"')) {
                    Send-WebResponse $ctx 400 'application/json' '{"ok":false,"error":"bad request"}'; continue
                }
                $id = $Matches[1]
                if (-not (@(Get-Servers) | Where-Object { $_.Alias -eq $alias })) {
                    Send-WebResponse $ctx 200 'application/json; charset=utf-8' ('{"id":"' + $id + '","ok":false,"error":"Сервер не найден в лаунчере."}'); continue
                }
                if (-not $sessions.ContainsKey($alias) -or $sessions[$alias].Proc.HasExited) {
                    if ($sessions.ContainsKey($alias)) { Close-ApiSession $sessions[$alias] ''; $sessions.Remove($alias) }
                    try { $sessions[$alias] = Open-ApiSession $alias }
                    catch { Send-WebResponse $ctx 200 'application/json; charset=utf-8' ('{"id":"' + $id + '","ok":false,"error":"Не удалось запустить ssh."}'); continue }
                }
                $s = $sessions[$alias]
                $s.Pending[$id] = $ctx
                $reqLine = (ConvertTo-AsciiJson ($body -replace "[\r\n]+", ' ')) + "`n"
                try {
                    if ($s.Ready) { Write-ApiLine $s $reqLine }
                    else { $s.Queue.Add($reqLine) }
                } catch {
                    Close-ApiSession $s 'Канал SSH закрыт.'; $sessions.Remove($alias)
                }
            } elseif ($path -eq '/app/iperf' -and $req.HttpMethod -eq 'POST') {
                $alias = [string]$req.QueryString['s']
                $srvObj = @(Get-Servers) | Where-Object { $_.Alias -eq $alias } | Select-Object -First 1
                if (-not $srvObj) { Send-WebResponse $ctx 200 'application/json; charset=utf-8' '{"ok":false,"error":"Сервер не найден в лаунчере."}'; continue }
                if (-not (Test-Path $IperfExe)) { Send-WebResponse $ctx 200 'application/json; charset=utf-8' '{"ok":false,"need_install":true,"error":"iperf3 на ПК не установлен."}'; continue }
                $dir = [string]$req.QueryString['dir']
                $rev = if ($dir -eq 'down') { ' -R -t 8' } elseif ($dir -eq 'udp') { $mb = [int]$req.QueryString['rate']; if ($mb -lt 2 -or $mb -gt 1000) { $mb = 20 }; " -u -b $($mb)M -R -t 5" } else { ' -t 8' }
                $iport = [int]$req.QueryString['port']; if ($iport -le 0) { $iport = 5201 }
                $psi = New-Object Diagnostics.ProcessStartInfo
                $psi.FileName = $IperfExe
                $psi.WorkingDirectory = $IperfDir
                $psi.Arguments = "-c $($srvObj.Host) -p $iport -J --connect-timeout 5000$rev"
                $psi.UseShellExecute = $false
                $psi.CreateNoWindow = $true
                $psi.RedirectStandardOutput = $true
                $psi.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
                try {
                    $p = [Diagnostics.Process]::Start($psi)
                    $jobs.Add(@{ Ctx = $ctx; Proc = $p; Out = $p.StandardOutput.ReadToEndAsync() })
                } catch {
                    Send-WebResponse $ctx 200 'application/json; charset=utf-8' ('{"ok":false,"error":' + (ConvertTo-Json "Не удалось запустить iperf3: $($_.Exception.Message)" -Compress) + '}')
                }
            } elseif ($path -eq '/app/iperf_install' -and $req.HttpMethod -eq 'POST') {
                $err = Install-Iperf
                if ($err) { Send-WebResponse $ctx 200 'application/json; charset=utf-8' ('{"ok":false,"error":' + (ConvertTo-Json $err -Compress) + '}') }
                else { Send-WebResponse $ctx 200 'application/json; charset=utf-8' '{"ok":true}' }
            } elseif ($path -eq '/app/close' -and $req.HttpMethod -eq 'POST') {
                $alias = [string]$req.QueryString['s']
                if ($sessions.ContainsKey($alias)) { Close-ApiSession $sessions[$alias] 'Переподключение.'; $sessions.Remove($alias) }
                Send-WebResponse $ctx 200 'application/json' '{"ok":true}'
            } else {
                Send-WebResponse $ctx 404 'text/plain' 'not found'
            }
          } catch {
            # ошибка одного запроса не останавливает веб-меню; подробности — в web.log
            $detail = "$([DateTime]::Now.ToString('s')) $($_.Exception.Message)`n$($_.InvocationInfo.PositionMessage)`n"
            try { [IO.File]::AppendAllText($webLog, $detail, [Text.UTF8Encoding]::new($false)) } catch { }
          }
        }
    } finally {
        foreach ($s in @($sessions.Values)) { Close-ApiSession $s 'Веб-меню остановлено.' }
        foreach ($j in $jobs.ToArray()) { try { if (-not $j.Proc.HasExited) { $j.Proc.Kill() } } catch { } }
        foreach ($j in $streamJobs.ToArray()) { try { if (-not $j.Proc.HasExited) { $j.Proc.Kill() } } catch { } }
        foreach ($st in $streams.ToArray()) { try { $st.Response.Close() } catch { } }
        try { $listener.Stop(); $listener.Close() } catch { }
        if ($tray) { try { $tray.Visible = $false; $tray.Dispose() } catch { } }
        Remove-Item -Force -ErrorAction SilentlyContinue $RunFile
    }
    if ($restart) {
        # обновлённый лаунчер — новым процессом, он откроет новую вкладку
        Start-Hidden
    }
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
        if (-not (Open-RunningInstance)) {
            # дальше kvnBox работает без окна: значок в трее, страница в браузере
            Start-Hidden
            Write-Ok 'kvnBox запущен: страница откроется в браузере, значок — в трее у часов.'
        } else {
            Write-Ok 'kvnBox уже был запущен — открыта его страница (обновление применится после закрытия kvnBox).'
        }
        return
    }
    if ($CliArgs.Count -ge 2 -and $CliArgs[0] -eq '-Connect') {
        # ярлыки серверов прежних версий: меню сервера в терминале
        $s = @(Get-Servers | Where-Object { $_.Alias -eq $CliArgs[1] })
        if ($s.Count) { Enter-Server $s[0]; return }
    }
    if ($CliArgs -notcontains '-Bg' -and $CliArgs -notcontains '-Console') {
        # запущен с окном (ярлык или перезапуск прежней версии): ярлыки — на запуск без окна,
        # сам kvnBox — заново в фоне; это окно закроется
        try {
            foreach ($l in @((Join-Path $DesktopDir 'kvnBox.lnk'), (Join-Path $StartMenuDir 'kvnBox.lnk'))) {
                if (Test-Path -LiteralPath $l) { New-Shortcut $l 'kvnBox' }
            }
        } catch { }
        if (-not (Open-RunningInstance)) { Start-Hidden }
        return
    }
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new()
    if (-not (Get-Tool 'ssh') -or -not (Get-Tool 'ssh-keygen')) {
        Show-Error "Не найден клиент OpenSSH (ssh.exe).`nУстановите: Параметры → Система → Дополнительные компоненты → «Клиент OpenSSH»."
        return
    }
    if (Open-RunningInstance) { return }   # уже запущен — только открыть страницу
    Import-OldServers
    Start-WebMenu
}

if (-not $env:KVNBOX_SETUP_TEST) { Start-Kvnbox $args }
