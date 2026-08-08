$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) {
    Write-Host "[comind] $Message" -ForegroundColor Green
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

$PidFile = Join-Path $DataRoot 'comind.pid'
$Stopped = $false
if (Test-Path $PidFile) {
    try {
        $TargetPid = [int](Get-Content $PidFile -ErrorAction Stop | Select-Object -First 1)
        Stop-Process -Id $TargetPid -Force -ErrorAction Stop
        Write-Info "Stopped backend process $TargetPid"
        $Stopped = $true
    }
    catch {
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

$Listeners = netstat -ano | Select-String ':8789 ' | Select-String 'LISTENING'
foreach ($line in $Listeners) {
    $parts = $line.ToString().Trim() -split '\s+'
    $TargetPid = [int]$parts[-1]
    Stop-Process -Id $TargetPid -Force -ErrorAction SilentlyContinue
    Write-Info "Stopped backend process $TargetPid"
    $Stopped = $true
}

if (-not $Stopped) {
    Write-Info 'No CoMind backend process was running.'
}
