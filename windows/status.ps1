$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

function Get-WritableDirectory([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if (-not $candidate) {
            continue
        }
        try {
            New-Item -ItemType Directory -Force -Path $candidate -ErrorAction Stop | Out-Null
            $probe = Join-Path $candidate '.write-test'
            Set-Content -Path $probe -Value 'ok' -Encoding UTF8 -ErrorAction Stop
            Remove-Item -Path $probe -Force -ErrorAction Stop
            return $candidate
        }
        catch {
        }
    }
    return (Join-Path $env:TEMP 'CoMind')
}

$DataRoot = if ($env:SMM_DATA_DIR) {
    $env:SMM_DATA_DIR
} else {
    Get-WritableDirectory @(
        (Join-Path $env:LOCALAPPDATA 'CoMind'),
        (Join-Path $HOME '.comind'),
        (Join-Path $env:TEMP 'CoMind')
    )
}
$MapRoot = if ($env:SMM_MAP_DIR) { $env:SMM_MAP_DIR } else { Join-Path $HOME 'comind-maps' }
$SessionRoot = Join-Path $DataRoot 'chat-sessions'
$KeysPath = Join-Path $DataRoot 'private\keys.json'
$LogFile = Join-Path $DataRoot 'comind.log'
$PidFile = Join-Path $DataRoot 'comind.pid'
$Url = 'http://127.0.0.1:8789'

function Show-Line([string]$Name, [string]$Value) {
    Write-Host ($Name.PadRight(18) + $Value)
}

function Get-StartupShortcutPath() {
    $startup = [Environment]::GetFolderPath('Startup')
    if (-not $startup) {
        $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    }
    return (Join-Path $startup 'CoMind.cmd')
}

Write-Host 'CoMind status' -ForegroundColor Cyan
Show-Line 'Project' $Root
Show-Line 'Data' $DataRoot
Show-Line 'Maps' $MapRoot
Show-Line 'Sessions' $SessionRoot
Show-Line 'Logs' $LogFile

$listener = netstat -ano | Select-String ':8789' | Select-String 'LISTENING' | Select-Object -First 1
if ($listener) {
    $pidText = ($listener.ToString().Trim() -split '\s+')[-1]
    Show-Line 'Port 8789' "LISTENING pid=$pidText"
} else {
    $pidText = ''
    Show-Line 'Port 8789' 'not listening'
}

if (Test-Path $PidFile) {
    $filePid = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pidText -and $filePid -and "$filePid" -ne "$pidText") {
        Show-Line 'Pid file' "$filePid (stale; active pid is $pidText)"
    } else {
        Show-Line 'Pid file' $filePid
    }
} else {
    Show-Line 'Pid file' 'missing'
}

try {
    $version = (Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/version" -TimeoutSec 3).Content
    Show-Line 'Backend' $version
} catch {
    Show-Line 'Backend' "not reachable: $($_.Exception.Message)"
}

try {
    $keys = (Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/keys" -TimeoutSec 3).Content
    Show-Line 'Keys' $keys
} catch {
    if (Test-Path $KeysPath) {
        Show-Line 'Keys' "file exists at $KeysPath"
    } else {
        Show-Line 'Keys' 'not configured'
    }
}

$LocalPi = Join-Path $DataRoot 'pi-runtime\node_modules\.bin\pi.cmd'
if (Test-Path $LocalPi) {
    try {
        $piVersion = & $LocalPi --version
        Show-Line 'Pi runtime' "$LocalPi ($piVersion)"
    } catch {
        Show-Line 'Pi runtime' "$LocalPi (version check failed)"
    }
} elseif ($env:APPDATA -and (Test-Path (Join-Path $env:APPDATA 'npm\pi.cmd'))) {
    Show-Line 'Pi runtime' (Join-Path $env:APPDATA 'npm\pi.cmd')
} else {
    Show-Line 'Pi runtime' 'missing'
}

if (Test-Path (Join-Path $Root 'dist\js\app.js')) {
    Show-Line 'Web assets' 'ready'
} else {
    Show-Line 'Web assets' 'missing, run install.cmd'
}

try {
    $task = Get-ScheduledTask -TaskName 'CoMind' -ErrorAction Stop
    Show-Line 'Startup task' $task.State
} catch {
    $query = & (Join-Path $env:SystemRoot 'System32\schtasks.exe') /Query /TN 'CoMind' /FO LIST 2>$null
    if ($LASTEXITCODE -eq 0) {
        Show-Line 'Startup task' 'installed'
    } elseif (Test-Path (Get-StartupShortcutPath)) {
        Show-Line 'Startup task' 'startup folder'
    } else {
        Show-Line 'Startup task' 'not installed'
    }
}

if (Test-Path $LogFile) {
    Write-Host ''
    Write-Host 'Recent log:' -ForegroundColor Cyan
    Get-Content $LogFile -Tail 20
}
