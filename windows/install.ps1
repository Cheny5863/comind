param(
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $Root

function Write-Info([string]$Message) {
    Write-Host "[install] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[install] $Message" -ForegroundColor Yellow
}

function Fail([string]$Message) {
    throw "[install] $Message"
}

function Get-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    return $null
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
    Fail 'No writable data directory was found.'
}

function Ensure-Command([string]$Name, [string]$WingetId, [string]$DisplayName, [string[]]$Fallbacks) {
    $path = Get-CommandPath $Name
    if ($path) {
        return $path
    }
    $path = Resolve-Executable $Fallbacks
    if ($path) {
        return $path
    }
    $winget = Get-CommandPath 'winget'
    if ($winget) {
        Write-Info "$DisplayName not found, installing via winget: $WingetId"
        & winget install --id $WingetId -e --accept-package-agreements --accept-source-agreements | Out-Host
        $path = Get-CommandPath $Name
        if ($path) {
            return $path
        }
        $path = Resolve-Executable $Fallbacks
        if ($path) {
            return $path
        }
    }
    Fail "$DisplayName is required. Install it manually, or make winget available and rerun this script."
}

$Python = Ensure-Command 'python' 'Python.Python.3.12' 'Python' @(
    "$env:LocalAppData\Programs\Python\Python312\python.exe",
    "$env:LocalAppData\Programs\Python\Python313\python.exe",
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python313\python.exe"
)
if (-not $Python) {
    $Python = Ensure-Command 'py' 'Python.Python.3.12' 'Python launcher' @()
}

$Node = Ensure-Command 'node' 'OpenJS.NodeJS.LTS' 'Node.js' @(
    "$env:ProgramFiles\nodejs\node.exe",
    "${env:ProgramFiles(x86)}\nodejs\node.exe",
    "$env:LocalAppData\Programs\nodejs\node.exe"
)
$Npm = Resolve-Executable @(
    (Join-Path (Split-Path $Node) 'npm.cmd'),
    (Join-Path $env:APPDATA 'npm\npm.cmd'),
    (Join-Path (Split-Path $Node) 'npm'),
    (Join-Path (Split-Path $Node) 'npm.ps1')
)
if (-not $Npm) {
    $Npm = Get-CommandPath 'npm'
}
if (-not $Npm) {
    Fail 'npm is required but was not found.'
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
$SessionRoot = Join-Path $DataRoot 'chat-sessions'
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'private') | Out-Null
New-Item -ItemType Directory -Force -Path $SessionRoot | Out-Null
New-Item -ItemType Directory -Force -Path $MapRoot | Out-Null

$Venv = Join-Path $Root '.venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Info 'Creating virtual environment...'
    & $Python -m venv $Venv
}

Write-Info 'Installing backend dependencies...'
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $Root 'requirements.txt')
if (Test-Path (Join-Path $Root 'requirements-dev.txt')) {
    & $VenvPython -m pip install -r (Join-Path $Root 'requirements-dev.txt')
}

$PiRuntime = Join-Path $DataRoot 'pi-runtime'
$LocalPi = Join-Path $PiRuntime 'node_modules\.bin\pi.cmd'
Write-Info "Installing pi coding agent into $PiRuntime..."
& $Npm install --prefix $PiRuntime --no-audit --fund=false @earendil-works/pi-coding-agent | Out-Host
if (-not (Test-Path $LocalPi)) {
    Fail "pi command was not installed at $LocalPi"
}
$env:PI_BIN = $LocalPi

Write-Info 'Building web assets...'
Push-Location (Join-Path $Root 'web')
try {
    if (Test-Path (Join-Path $Root 'web\package-lock.json')) {
        & $Npm ci --no-audit --fund=false
    } else {
        & $Npm install --no-audit --fund=false
    }
    & $Npm run build
}
finally {
    Pop-Location
}

$PidFile = Join-Path $DataRoot 'comind.pid'
$LogFile = Join-Path $DataRoot 'comind.log'
if (Test-Path $PidFile) {
    try {
        $OldPid = [int](Get-Content $PidFile -ErrorAction Stop | Select-Object -First 1)
        Stop-Process -Id $OldPid -Force -ErrorAction Stop
        Write-Info "Stopped previous instance $OldPid"
    }
    catch {
        Write-Warn 'No running instance to stop.'
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$env:SMM_PORT = '8789'
$env:SMM_DATA_DIR = $DataRoot
$env:SMM_MAP_DIR = $MapRoot
$env:SMM_CHAT_SESSION_DIR = $SessionRoot
$piShim = $LocalPi

if ($NoLaunch) {
    Write-Info 'Build completed. No backend launched because -NoLaunch was set.'
    exit 0
}

Write-Info 'Starting backend...'
$Process = Start-Process -FilePath $VenvPython -ArgumentList 'backend.py' -WorkingDirectory $Root -PassThru -WindowStyle Hidden

$Url = "http://127.0.0.1:$($env:SMM_PORT)"
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/version" -TimeoutSec 2 | Out-Null
        break
    }
    catch {
    }
}

$ListenerPid = Get-ListeningPid $env:SMM_PORT
if (-not $ListenerPid) {
    $ListenerPid = $Process.Id
}
try {
    $ListenerPid | Set-Content $PidFile -ErrorAction Stop
}
catch {
    Write-Warn "Could not write pid file: $PidFile"
}
if ($piShim) {
    Write-Info "Using pi shim: $piShim"
}
Write-Info 'Deployment complete.'
Write-Info 'Open http://127.0.0.1:8789'
Write-Info "PID: $ListenerPid"
Write-Info "Logs: $LogFile"
