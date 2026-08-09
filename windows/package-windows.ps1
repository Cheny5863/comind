param(
    [string]$ReleaseDir = "dist-release",
    [string]$NodeVersion = "22.19.0",
    [string]$NodeZip = "",
    [switch]$SkipWebBuild,
    [switch]$SkipDevZip
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
Set-Location $Root

function Write-Info([string]$Message) {
    Write-Host "[package] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[package] $Message" -ForegroundColor Yellow
}

function Fail([string]$Message) {
    throw "[package] $Message"
}

function Resolve-Executable([string[]]$Candidates) {
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Require-Command([string]$Name, [string[]]$Fallbacks = @()) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $fallback = Resolve-Executable $Fallbacks
    if ($fallback) {
        return $fallback
    }
    Fail "$Name is required."
}

function Assert-PathInsideRoot([string]$Path, [string]$AllowedRoot) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullRoot = [System.IO.Path]::GetFullPath($AllowedRoot).TrimEnd('\', '/')
    $prefix = $fullRoot + [System.IO.Path]::DirectorySeparatorChar

    if (-not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "Refusing to remove path outside release directory: $fullPath"
    }
}

function Remove-TreeRobust([string]$Target) {
    if (-not (Test-Path $Target)) {
        return
    }

    Assert-PathInsideRoot $Target $ReleaseRoot
    Write-Info "Clearing previous output: $Target"

    $emptyDir = Join-Path $ReleaseRoot ("_empty-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $emptyDir | Out-Null
    robocopy $emptyDir $Target /MIR /NFL /NDL /NJH /NJS /NP | Out-Null
    $RobocopyCode = $LASTEXITCODE
    Remove-Item -Recurse -Force -LiteralPath $emptyDir -ErrorAction SilentlyContinue
    if ($RobocopyCode -ge 8) {
        Fail "robocopy failed while clearing $Target with exit code $RobocopyCode"
    }

    Remove-Item -Recurse -Force -LiteralPath $Target -ErrorAction SilentlyContinue
}

function Copy-Tree([string]$Source, [string]$Destination) {
    if (Test-Path $Destination) {
        Remove-TreeRobust $Destination
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item -Recurse -Force -Path (Join-Path $Source '*') -Destination $Destination
}

function Resolve-SystemTar {
    # 必须用 Windows 自带 bsdtar：Git for Windows 的 /usr/bin/tar 会把
    # "C:\..." 路径当 URL 主机名解析（Cannot connect to C:），导致打包失败
    foreach ($cand in @(
        (Join-Path $env:SystemRoot 'System32\tar.exe'),
        (Join-Path $env:SystemRoot 'Sysnative\tar.exe')
    )) {
        if (Test-Path $cand) {
            return (Get-Item -LiteralPath $cand)
        }
    }
    return $null
}

function New-ZipArchive([string]$SourceDir, [string]$ZipPath) {
    if (Test-Path $ZipPath) {
        Remove-Item -Force -LiteralPath $ZipPath
    }

    $sourceItem = Get-Item -LiteralPath $SourceDir
    $parent = $sourceItem.Parent.FullName
    $leaf = $sourceItem.Name
    $tar = Resolve-SystemTar

    if (-not $tar) {
        Fail 'Windows built-in tar.exe was not found (requires Windows 10 1803+); create the zip manually.'
    }

    Write-Info "Creating zip with tar.exe: $ZipPath"
    Push-Location $parent
    try {
        & $tar.FullName -a -c -f $ZipPath $leaf
        if ($LASTEXITCODE -ne 0) {
            Fail "tar.exe failed to create zip archive: $ZipPath"
        }
    }
    finally {
        Pop-Location
    }
}

function Assert-ZipContainsEntries([string]$ZipPath, [string[]]$ExpectedEntries) {
    $tar = Resolve-SystemTar
    if (-not $tar) {
        Fail 'Windows built-in tar.exe was not found, so the zip archive could not be verified.'
    }

    $entries = & $tar.FullName -tf $ZipPath
    if ($LASTEXITCODE -ne 0) {
        Fail "tar.exe failed to read zip archive for verification: $ZipPath"
    }

    foreach ($entry in $ExpectedEntries) {
        if ($entries -notcontains $entry) {
            Fail "Zip archive is missing required runtime file: $entry"
        }
    }
}

function Assert-ZipEntryPathLengths([string]$ZipPath, [int]$MaxEntryLength = 190) {
    $tar = Resolve-SystemTar
    if (-not $tar) {
        Fail 'Windows built-in tar.exe was not found, so the zip archive could not be verified.'
    }

    $entries = & $tar.FullName -tf $ZipPath
    if ($LASTEXITCODE -ne 0) {
        Fail "tar.exe failed to read zip archive for path-length verification: $ZipPath"
    }

    $longest = $entries |
        ForEach-Object { [PSCustomObject]@{ Entry = $_; Length = $_.Length } } |
        Sort-Object Length -Descending |
        Select-Object -First 1
    if ($longest -and $longest.Length -gt $MaxEntryLength) {
        Fail "Zip path is too long for beginner-friendly extraction ($($longest.Length) > $MaxEntryLength): $($longest.Entry)"
    }
}

function Normalize-PathForCompare([string]$Path) {
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-PathUnder([string]$Path, [string]$RootPath) {
    $full = Normalize-PathForCompare $Path
    $root = Normalize-PathForCompare $RootPath
    return $full.Equals($root, [System.StringComparison]::OrdinalIgnoreCase) -or
        $full.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-ProtectedPackageOwnPath([string]$Path, [string[]]$ProtectedPackageRoots) {
    foreach ($root in $ProtectedPackageRoots) {
        if (-not $root) {
            continue
        }
        if (Test-PathUnder $Path $root) {
            $dependencyRoot = Join-Path $root 'node_modules'
            return -not (Test-PathUnder $Path $dependencyRoot)
        }
    }
    return $false
}

function Test-RemovableNodeFile([string]$Name) {
    $lower = $Name.ToLowerInvariant()
    return $lower.EndsWith('.d.ts') -or
        $lower.EndsWith('.d.ts.map') -or
        $lower.EndsWith('.map') -or
        $lower.EndsWith('.md') -or
        $lower.EndsWith('.markdown') -or
        $lower.EndsWith('.ts')
}

function Optimize-NodeModulesForZip([string]$NodeModulesDir, [string[]]$ProtectedPackageRoots = @()) {
    if (-not (Test-Path $NodeModulesDir)) {
        return
    }

    Write-Info 'Removing npm package metadata that is not needed at runtime...'
    Get-ChildItem -LiteralPath $NodeModulesDir -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Where-Object {
            -not (Test-ProtectedPackageOwnPath $_.FullName $ProtectedPackageRoots) -and
            $_.Name -in @('@types', '.github', '.vscode', 'coverage', 'dist-types', 'test', 'tests', '__tests__', 'docs', 'example', 'examples')
        } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            Remove-Item -Recurse -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue
        }

    Get-ChildItem -LiteralPath $NodeModulesDir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object {
            -not (Test-ProtectedPackageOwnPath $_.FullName $ProtectedPackageRoots) -and
            (Test-RemovableNodeFile $_.Name)
        } |
        ForEach-Object {
            Remove-Item -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue
        }

    Get-ChildItem -LiteralPath $NodeModulesDir -Recurse -Directory -Force -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object {
            try {
                if (-not (Test-ProtectedPackageOwnPath $_.FullName $ProtectedPackageRoots) -and
                    -not (Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
                    Remove-Item -Force -LiteralPath $_.FullName -ErrorAction SilentlyContinue
                }
            } catch {}
        }
}

function Optimize-PortableNodeForZip([string]$NodeDir) {
    if (-not (Test-Path $NodeDir)) {
        return
    }

    Write-Info 'Removing npm from portable Node.js runtime; CoMind only needs node.exe after packaging...'
    $removeNames = @(
        'node_modules',
        'npm',
        'npm.cmd',
        'npm.ps1',
        'npx',
        'npx.cmd',
        'npx.ps1',
        'corepack',
        'corepack.cmd',
        'corepack.ps1',
        'install_tools.bat',
        'nodevars.bat'
    )
    foreach ($name in $removeNames) {
        $target = Join-Path $NodeDir $name
        if (Test-Path $target) {
            Remove-Item -Recurse -Force -LiteralPath $target -ErrorAction SilentlyContinue
        }
    }
}

$Version = (Get-Content -Path (Join-Path $Root 'VERSION') -Raw).Trim()
if (-not $Version) {
    $Version = 'dev'
}

$ReleaseRoot = Join-Path $Root $ReleaseDir
$BuildRoot = Join-Path $ReleaseRoot '_build'
$PyDist = Join-Path $BuildRoot 'pyinstaller-dist'
$PyWork = Join-Path $BuildRoot 'pyinstaller-work'
$UserRoot = Join-Path $ReleaseRoot "CoMind"
$DevRoot = Join-Path $ReleaseRoot "comind-$Version-windows-x86_64-dev"

New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
if (Test-Path $BuildRoot) {
    Remove-TreeRobust $BuildRoot
}
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

$Python = Resolve-Executable @(
    (Join-Path $Root '.venv\Scripts\python.exe'),
    (Get-Command python -ErrorAction SilentlyContinue).Source,
    (Get-Command py -ErrorAction SilentlyContinue).Source
)
if (-not $Python) {
    Fail 'Python is required. Run install.cmd first or install Python 3.12+.'
}

$Npm = Resolve-Executable @(
    (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'),
    (Join-Path $env:APPDATA 'npm\npm.cmd'),
    (Get-Command npm -ErrorAction SilentlyContinue).Source
)
if (-not $Npm) {
    Fail 'npm is required to build web assets and install pi runtime.'
}

if (-not $SkipWebBuild) {
    Write-Info 'Installing frontend dependencies and building dist...'
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
}

if (-not (Test-Path (Join-Path $Root 'dist\js\app.js'))) {
    Fail 'Built web assets are missing. Run install.cmd or rerun package-windows.ps1 without -SkipWebBuild.'
}

Write-Info 'Installing backend build dependencies...'
& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root 'requirements.txt')
& $Python -m pip install pyinstaller

Write-Info 'Building CoMind.exe with PyInstaller...'
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --noconsole `
    --name CoMind `
    --distpath $PyDist `
    --workpath $PyWork `
    --add-data "dist;dist" `
    --add-data "ai-assistant;ai-assistant" `
    --add-data "pi-ext;pi-ext" `
    --add-data "index.html;." `
    --add-data "map-switcher.js;." `
    --add-data "VERSION;." `
    --collect-submodules uvicorn `
    --collect-submodules fastapi `
    backend.py

$BuiltExeDir = Join-Path $PyDist 'CoMind'
if (-not (Test-Path (Join-Path $BuiltExeDir 'CoMind.exe'))) {
    Fail 'PyInstaller did not produce CoMind.exe.'
}

Write-Info "Preparing user package: $UserRoot"
Copy-Tree $BuiltExeDir $UserRoot

$NodeDir = Join-Path $UserRoot 'n'
if ($NodeZip) {
    if (-not (Test-Path $NodeZip)) {
        Fail "Node zip not found: $NodeZip"
    }
    $ResolvedNodeZip = Resolve-Path $NodeZip
} else {
    $NodeCache = Join-Path $BuildRoot "node-v$NodeVersion-win-x64.zip"
    $NodeUrl = "https://nodejs.org/dist/v$NodeVersion/node-v$NodeVersion-win-x64.zip"
    Write-Info "Downloading portable Node.js $NodeVersion..."
    Invoke-WebRequest -UseBasicParsing -Uri $NodeUrl -OutFile $NodeCache
    $ResolvedNodeZip = $NodeCache
}

$NodeExtract = Join-Path $BuildRoot 'n'
Expand-Archive -Path $ResolvedNodeZip -DestinationPath $NodeExtract -Force
$ExtractedNodeDir = Get-ChildItem -Path $NodeExtract -Directory | Select-Object -First 1
if (-not $ExtractedNodeDir) {
    Fail 'Portable Node.js zip did not contain a directory.'
}
Copy-Tree $ExtractedNodeDir.FullName $NodeDir

$PackagedNpm = Join-Path $NodeDir 'npm.cmd'
if (-not (Test-Path $PackagedNpm)) {
    Fail 'Portable Node.js did not include npm.cmd.'
}

Write-Info 'Installing pi coding agent into user package...'
$PiRuntime = Join-Path $UserRoot 'p'
& $PackagedNpm install --prefix $PiRuntime --omit=dev --no-audit --fund=false @earendil-works/pi-coding-agent

$PiInstalledPackageDir = Join-Path $PiRuntime 'node_modules\@earendil-works\pi-coding-agent'
$PiPackageDir = Join-Path $PiRuntime 'a'
if (-not (Test-Path $PiInstalledPackageDir)) {
    Fail 'pi coding agent package was not installed into the user package.'
}
if (Test-Path $PiPackageDir) {
    Remove-TreeRobust $PiPackageDir
}
Move-Item -LiteralPath $PiInstalledPackageDir -Destination $PiPackageDir
$PiTopNodeModules = Join-Path $PiRuntime 'node_modules'
if (Test-Path $PiTopNodeModules) {
    Remove-Item -Recurse -Force -LiteralPath $PiTopNodeModules -ErrorAction SilentlyContinue
}
foreach ($name in @('package.json', 'package-lock.json')) {
    $target = Join-Path $PiRuntime $name
    if (Test-Path $target) {
        Remove-Item -Force -LiteralPath $target -ErrorAction SilentlyContinue
    }
}

$PiCli = Join-Path $PiPackageDir 'dist\cli.js'
if (-not (Test-Path $PiCli)) {
    Fail 'pi coding agent CLI was not installed into the user package.'
}
Optimize-NodeModulesForZip (Join-Path $PiPackageDir 'node_modules') @($PiPackageDir)
if (-not (Test-Path $PiCli)) {
    Fail 'pi coding agent CLI was removed while optimizing the user package.'
}
Optimize-PortableNodeForZip $NodeDir

Set-Content -Encoding ASCII -Path (Join-Path $UserRoot 'smm-pi.cmd') -Value @(
    '@echo off',
    'setlocal',
    'set "ROOT=%~dp0"',
    '"%ROOT%n\node.exe" "%ROOT%p\a\dist\cli.js" %*'
)

$oldSkipVersionCheck = $env:PI_SKIP_VERSION_CHECK
$oldTelemetry = $env:PI_TELEMETRY
try {
    $env:PI_SKIP_VERSION_CHECK = '1'
    $env:PI_TELEMETRY = '0'
    $PiCheckOut = Join-Path $BuildRoot 'pi-help.out.txt'
    $PiCheckErr = Join-Path $BuildRoot 'pi-help.err.txt'
    $SmmPiCmd = Join-Path $UserRoot 'smm-pi.cmd'
    $PiCheckCmd = '"' + $SmmPiCmd + '" --help > "' + $PiCheckOut + '" 2> "' + $PiCheckErr + '"'
    & cmd.exe /d /c $PiCheckCmd
    if ($LASTEXITCODE -ne 0) {
        $piError = ''
        if (Test-Path $PiCheckErr) {
            $piError = (Get-Content -LiteralPath $PiCheckErr -Raw -ErrorAction SilentlyContinue).Trim()
        }
        if (-not $piError -and (Test-Path $PiCheckOut)) {
            $piError = (Get-Content -LiteralPath $PiCheckOut -Raw -ErrorAction SilentlyContinue).Trim()
        }
        Fail "Packaged pi coding agent CLI failed to start. $piError"
    }
}
finally {
    $env:PI_SKIP_VERSION_CHECK = $oldSkipVersionCheck
    $env:PI_TELEMETRY = $oldTelemetry
}

Set-Content -Encoding ASCII -Path (Join-Path $UserRoot 'Start CoMind.cmd') -Value @(
    '@echo off',
    'setlocal',
    'cd /d "%~dp0"',
    'set PYTHONUTF8=1',
    'start "" "%~dp0CoMind.exe"'
)

Set-Content -Encoding ASCII -Path (Join-Path $UserRoot 'Stop CoMind.cmd') -Value @(
    '@echo off',
    'setlocal',
    'for /f "tokens=5" %%p in (''netstat -ano ^| findstr ":8789 " ^| findstr "LISTENING"'') do taskkill /PID %%p /F >nul 2>nul',
    'echo CoMind stopped.',
    'pause'
)

Set-Content -Encoding UTF8 -Path (Join-Path $UserRoot 'README-Windows-User.md') -Value @(
    '# CoMind Windows Click-to-Run Package',
    '',
    'For users who do not want to use the command line.',
    '',
    '## How to use',
    '',
    '1. Double-click `Start CoMind.cmd`, or double-click `CoMind.exe` directly.',
    '2. Your browser opens `http://127.0.0.1:8789` automatically.',
    '3. On first use, open model settings in the AI panel, paste your DeepSeek or Kimi API key, and save.',
    '4. To stop CoMind, double-click `Stop CoMind.cmd`.',
    '',
    '## Windows security prompt',
    '',
    'If Windows SmartScreen warns about an unsigned app, only continue if this package came from a trusted CoMind release.',
    '',
    '## Data locations',
    '',
    '- Mind maps: `%USERPROFILE%\comind-maps`',
    '- API keys and chat sessions: `%LOCALAPPDATA%\CoMind`',
    '- Error log: `%LOCALAPPDATA%\CoMind\comind-exe.log`',
    '',
    'These files stay on this computer. Deleting this app folder does not automatically delete your maps or API keys.',
    '',
    '## Troubleshooting',
    '',
    'If the page shows `Internal Server Error`, open PowerShell and run:',
    '',
    '```powershell',
    'Get-Content "$env:LOCALAPPDATA\CoMind\comind-exe.log" -Tail 80',
    '```',
    '',
    'Send that output to the maintainer.'
)

$requiredUserFiles = @(
    (Join-Path $UserRoot 'CoMind.exe'),
    (Join-Path $UserRoot '_internal\index.html'),
    (Join-Path $UserRoot '_internal\map-switcher.js'),
    $PiCli
)
foreach ($requiredFile in $requiredUserFiles) {
    if (-not (Test-Path $requiredFile)) {
        Fail "User package is missing required runtime file: $requiredFile"
    }
}

$UserZip = Join-Path $ReleaseRoot "comind-$Version-windows-x86_64.zip"
New-ZipArchive $UserRoot $UserZip
Assert-ZipContainsEntries $UserZip @(
    'CoMind/CoMind.exe',
    'CoMind/_internal/index.html',
    'CoMind/_internal/map-switcher.js',
    'CoMind/p/a/dist/cli.js'
)
Assert-ZipEntryPathLengths $UserZip 190
Write-Info "User package ready: $UserZip"

if (-not $SkipDevZip) {
    Write-Info "Preparing developer source package: $DevRoot"
    if (Test-Path $DevRoot) {
        Remove-TreeRobust $DevRoot
    }
    New-Item -ItemType Directory -Force -Path $DevRoot | Out-Null
    robocopy $Root $DevRoot /E /XD .git .venv node_modules dist-release web\node_modules simple-mind-map\node_modules /XF *.log *.pyc | Out-Host
    $RobocopyCode = $LASTEXITCODE
    if ($RobocopyCode -ge 8) {
        Fail "robocopy failed with exit code $RobocopyCode"
    }
    $DevZip = Join-Path $ReleaseRoot "comind-$Version-windows-x86_64-dev.zip"
    New-ZipArchive $DevRoot $DevZip
    Write-Info "Developer package ready: $DevZip"
}

Write-Info 'Done.'
