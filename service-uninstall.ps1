$ErrorActionPreference = 'Stop'
$TaskName = 'CoMind'
$Root = Split-Path -Parent $PSCommandPath

function Write-Info([string]$Message) {
    Write-Host "[comind-service] $Message" -ForegroundColor Green
}

function Get-StartupShortcutPath() {
    $startup = [Environment]::GetFolderPath('Startup')
    if (-not $startup) {
        $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    }
    return (Join-Path $startup 'CoMind.cmd')
}

try {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Info "Removed startup task: $TaskName"
}
catch {
    $SchTasks = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    & $SchTasks /Delete /TN $TaskName /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Removed startup task: $TaskName"
    } else {
        Write-Info "Startup task was not installed: $TaskName"
    }
}

$StartupShortcut = Get-StartupShortcutPath
if (Test-Path $StartupShortcut) {
    Remove-Item -Path $StartupShortcut -Force
    Write-Info "Removed startup folder shortcut: $StartupShortcut"
}

& (Join-Path $Root 'stop.ps1')
