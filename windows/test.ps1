param(
    [switch]$AI
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$Python = Join-Path $Root '.venv\Scripts\python.exe'

function Write-Info([string]$Message) {
    Write-Host "[test] $Message" -ForegroundColor Green
}

function Write-Warn([string]$Message) {
    Write-Host "[test] $Message" -ForegroundColor Yellow
}

function Assert-Ok([bool]$Condition, [string]$Message) {
    if ($Condition) {
        Write-Info "OK: $Message"
    } else {
        Write-Warn "FAIL: $Message"
        $script:Failed = $true
    }
}

function Resolve-TestTempRoot {
    $candidates = @()
    $candidates += (Join-Path $Root '.test-tmp')
    if ($env:LOCALAPPDATA) {
        $candidates += (Join-Path $env:LOCALAPPDATA 'CoMind\test-tmp')
    }
    if ($env:TEMP) {
        $candidates += (Join-Path $env:TEMP 'CoMind\test-tmp')
    }

    foreach ($candidate in $candidates) {
        try {
            New-Item -ItemType Directory -Force -Path $candidate -ErrorAction Stop | Out-Null
            $probe = Join-Path $candidate (".write-test-" + [System.Guid]::NewGuid().ToString("N"))
            New-Item -ItemType File -Path $probe -ErrorAction Stop | Out-Null
            Remove-Item -Force -LiteralPath $probe -ErrorAction SilentlyContinue
            return $candidate
        } catch {
            Write-Warn "Temp directory is not writable: $candidate"
        }
    }

    Write-Warn 'No writable pytest temp directory was found.'
    return $null
}

$script:Failed = $false

Assert-Ok (Test-Path $Python) 'Python virtual environment exists'
Assert-Ok (Test-Path (Join-Path $Root 'dist\js\app.js')) 'frontend dist exists'

if (Test-Path $Python) {
    & $Python -B -c "import ast, pathlib; [ast.parse(pathlib.Path(p).read_text(encoding='utf-8-sig'), filename=p) for p in ['backend.py','chat_service.py']]; print('python syntax ok')"
    Assert-Ok ($LASTEXITCODE -eq 0) 'Python syntax check'
}

try {
    $version = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8789/api/version' -TimeoutSec 5).Content
    Write-Info "Backend: $version"
} catch {
    Write-Warn "Backend is not reachable. Run start.cmd first. $($_.Exception.Message)"
    $script:Failed = $true
}

try {
    $keys = (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8789/api/keys' -TimeoutSec 5).Content | ConvertFrom-Json
    Assert-Ok ([bool]$keys.configured.deepseek) 'DeepSeek key configured'
} catch {
    Write-Warn "Could not read key status: $($_.Exception.Message)"
    $script:Failed = $true
}

try {
    $items = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8789/api/list' -TimeoutSec 5
    Write-Info "Map list: $($items.Content)"
} catch {
    Write-Warn "Could not list maps: $($_.Exception.Message)"
    $script:Failed = $true
}

try {
    $models = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8789/api/chat/1.smm.json/models' -TimeoutSec 20
    Assert-Ok ($models.Content -match 'deepseek') 'AI model endpoint responds'
} catch {
    Write-Warn "AI model endpoint failed: $($_.Exception.Message)"
    $script:Failed = $true
}

if (Test-Path $Python) {
    & $Python -m pytest --version *> $null
    if ($LASTEXITCODE -eq 0) {
        & $Python -c "import httpx2" *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn 'pytest dependency httpx2 is missing. Run install.cmd to install requirements.txt.'
            $script:Failed = $true
        } else {
            Write-Info 'Running pytest...'
            $pytestRoot = Resolve-TestTempRoot
            if ($pytestRoot) {
                $pytestTemp = Join-Path $pytestRoot ("pytest-" + [System.Guid]::NewGuid().ToString("N"))
                $env:PYTHONPYCACHEPREFIX = Join-Path $pytestRoot 'pycache'
                & $Python -m pytest tests -p no:cacheprovider --basetemp $pytestTemp
                Assert-Ok ($LASTEXITCODE -eq 0) 'pytest'
            } else {
                $script:Failed = $true
            }
        }
    } else {
        Write-Warn 'pytest is not installed; install.cmd installs it from requirements.txt.'
    }
}

if ($AI -and (Test-Path $Python)) {
    Write-Info 'Running one live AI prompt through the backend objects...'
    $env:SMM_KEYS_PATH = Join-Path $env:LOCALAPPDATA 'CoMind\private\keys.json'
    $env:PYTHONIOENCODING = 'utf-8'
    & $Python -B -c "import sys, time, queue, backend
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
s=backend.chat_manager.get_or_spawn('1.smm.json'); q=s.subscribe(True); backend.chat_manager.prompt('1.smm.json','Reply with OK only', lang='en'); end=time.time()+60; ok=False
while time.time()<end:
    try:
        line=q.get(timeout=1)
        print(line)
        if line and ('text_delta' in line or 'message_end' in line and 'assistant' in line and 'errorMessage' not in line): ok=True
        if line and 'agent_end' in line: break
        if line is None: break
    except queue.Empty:
        pass
raise SystemExit(0 if ok else 1)"
    Assert-Ok ($LASTEXITCODE -eq 0) 'live AI prompt produced text'
}

if ($script:Failed) {
    exit 1
}
Write-Info 'All requested checks passed.'
