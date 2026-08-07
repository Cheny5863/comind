param(
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$TaskName = 'CoMind'
$StartScript = Join-Path $Root 'start.ps1'
$ServiceRunner = Join-Path $Root 'service-run.cmd'
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$BuiltApp = Join-Path $Root 'dist\js\app.js'

function Write-Info([string]$Message) {
    Write-Host "[comind-service] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[comind-service] $Message" -ForegroundColor Yellow
}

function Get-StartupShortcutPath() {
    $startup = [Environment]::GetFolderPath('Startup')
    if (-not $startup) {
        $startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
    }
    return (Join-Path $startup 'CoMind.cmd')
}

if (-not (Test-Path $StartScript)) {
    throw "start.ps1 not found at $StartScript"
}
if (-not (Test-Path $ServiceRunner)) {
    throw "service-run.cmd not found at $ServiceRunner"
}
if (-not (Test-Path $Python)) {
    throw "Python venv not found. Run install.cmd first."
}
if (-not (Test-Path $BuiltApp)) {
    throw "Built web assets are missing. Run install.cmd first."
}

$PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$Args = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -NoBrowser"
$Installed = $false
$InstallMethod = ''

try {
    $Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $Args -WorkingDirectory $Root
    $Trigger = New-ScheduledTaskTrigger -AtLogOn
    $Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 0)
    $CurrentUser = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
    $Principal = New-ScheduledTaskPrincipal -UserId $CurrentUser -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
    $Installed = $true
    $InstallMethod = 'scheduled task'
}
catch {
    Write-Warn "ScheduledTasks module failed: $($_.Exception.Message)"
    Write-Warn 'Trying schtasks.exe fallback...'
    $SchTasks = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    $TaskRun = "`"$ServiceRunner`""
    & $SchTasks /Create /TN $TaskName /SC ONLOGON /TR $TaskRun /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $Installed = $true
        $InstallMethod = 'schtasks'
    } else {
        Write-Warn 'schtasks.exe fallback failed. Using the current-user Startup folder...'
        $StartupShortcut = Get-StartupShortcutPath
        try {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StartupShortcut) -ErrorAction Stop | Out-Null
            Set-Content -Path $StartupShortcut -Encoding ASCII -ErrorAction Stop -Value @(
                '@echo off',
                ('call "' + $ServiceRunner + '"')
            )
        }
        catch {
            throw "Could not write Startup folder shortcut at $StartupShortcut. Try running this script from your normal desktop PowerShell, or from an elevated PowerShell."
        }
        $Installed = $true
        $InstallMethod = 'startup folder'
    }
}

if (-not $Installed) {
    throw "Could not create startup task."
}
Write-Info "Installed current-user startup task: $TaskName ($InstallMethod)"

if (-not $NoStart) {
    if ($InstallMethod -eq 'startup folder') {
        Start-Process -FilePath $ServiceRunner -WindowStyle Hidden
    } else {
        try {
            Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        }
        catch {
            & (Join-Path $env:SystemRoot 'System32\schtasks.exe') /Run /TN $TaskName | Out-Null
        }
    }
    Write-Info 'Started CoMind in the background.'
}

Write-Info 'Open http://127.0.0.1:8789'
