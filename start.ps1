param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
Set-Location $Root

function Write-Info([string]$Message) {
    Write-Host "[comind] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[comind] $Message" -ForegroundColor Yellow
}

function Resolve-Executable([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Get-ListeningPid([string]$Port) {
    $listeners = netstat -ano | Select-String 'LISTENING'
    foreach ($line in $listeners) {
        $parts = $line.ToString().Trim() -split '\s+'
        if ($parts.Length -ge 5 -and $parts[1] -match ":$Port$") {
            return $parts[-1]
        }
    }
    return $null
}

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
    throw "No writable directory found."
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
$MapRoot = if ($env:SMM_MAP_DIR) {
    $env:SMM_MAP_DIR
} else {
    Get-WritableDirectory @(
        (Join-Path $HOME 'comind-maps'),
        (Join-Path $DataRoot 'maps')
    )
}
$SessionRoot = if ($env:SMM_CHAT_SESSION_DIR) { $env:SMM_CHAT_SESSION_DIR } else { Join-Path $DataRoot 'chat-sessions' }
$PrivateRoot = Join-Path $DataRoot 'private'
$LogFile = Join-Path $DataRoot 'comind.log'
$PidFile = Join-Path $DataRoot 'comind.pid'

New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
New-Item -ItemType Directory -Force -Path $SessionRoot | Out-Null
New-Item -ItemType Directory -Force -Path $PrivateRoot | Out-Null
New-Item -ItemType Directory -Force -Path $MapRoot | Out-Null

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw "Python venv not found. Run install.cmd first."
}

$BuiltApp = Join-Path $Root 'dist\js\app.js'
if (-not (Test-Path $BuiltApp)) {
    throw "Built web assets are missing. Run this once: cd web; npm.cmd run build"
}

if (-not $env:PI_BIN) {
    $LocalPi = Join-Path $DataRoot 'pi-runtime\node_modules\.bin\pi.cmd'
    $Pi = Resolve-Executable @(
        $LocalPi,
        (Join-Path $env:APPDATA 'npm\pi.cmd'),
        (Join-Path $env:APPDATA 'npm\pi.exe'),
        (Join-Path $env:APPDATA 'npm\pi.ps1'),
        (Join-Path $env:APPDATA 'npm\pi')
    )
    if ($Pi) {
        $env:PI_BIN = $Pi
    }
}

if (Test-Path $PidFile) {
    try {
        $OldPid = [int](Get-Content $PidFile -ErrorAction Stop | Select-Object -First 1)
        $OldProc = Get-Process -Id $OldPid -ErrorAction SilentlyContinue
        if ($OldProc -and $OldProc.ProcessName -in @('python', 'CoMind')) {
            Stop-Process -Id $OldPid -Force -ErrorAction Stop
            Write-Info "Stopped previous instance $OldPid"
        }
        else {
            Write-Warn "Ignoring stale pid file: $OldPid is not a CoMind process."
        }
    }
    catch {
        Write-Warn 'No previous instance was running.'
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$env:SMM_PORT = if ($env:SMM_PORT) { $env:SMM_PORT } else { '8789' }
$env:SMM_DATA_DIR = $DataRoot
$env:SMM_MAP_DIR = $MapRoot
$env:SMM_CHAT_SESSION_DIR = $SessionRoot

Write-Info 'Starting backend...'
$Process = Start-Process -FilePath $Python -ArgumentList 'backend.py' -WorkingDirectory $Root -PassThru -WindowStyle Hidden

$Url = "http://127.0.0.1:$($env:SMM_PORT)"
$Ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/version" -TimeoutSec 2 | Out-Null
        $Ready = $true
        break
    }
    catch {
    }
}

if (-not $Ready) {
    Write-Warn "Backend did not become ready. Check log: $LogFile"
    exit 1
}

$ListenerPid = Get-ListeningPid $env:SMM_PORT
if ($ListenerPid -and $ListenerPid -ne $Process.Id) {
    Write-Warn "Port $($env:SMM_PORT) is held by PID $ListenerPid, not the process we started; pid file not written."
    $ListenerPid = $null
}
if (-not $ListenerPid) {
    $ListenerPid = $Process.Id
}
try {
    $ListenerPid | Set-Content $PidFile -ErrorAction Stop
}
catch {
    Write-Warn "Could not write pid file: $PidFile"
}

Write-Info "Open $Url"
Write-Info "Data: $DataRoot"
Write-Info "Maps: $MapRoot"
Write-Info "Logs: $LogFile"
if (-not $NoBrowser) {
    Start-Process $Url
}
