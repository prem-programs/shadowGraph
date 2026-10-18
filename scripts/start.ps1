# Start ShadowGraph on Windows.
#   Docker (recommended):   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#   Without Docker:         powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Mode local
#     (local mode needs FALKORDB_URL in .env pointing at FalkorDB Cloud, because FalkorDB has no Windows build)
param([ValidateSet("docker", "local")][string]$Mode = "docker")
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example (all values optional)."
}

if ($Mode -eq "docker") {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "Docker is not installed. Install Docker Desktop (https://www.docker.com/products/docker-desktop/),"
        Write-Host "start it, then run this script again. Or use -Mode local with FalkorDB Cloud."
        exit 1
    }
    docker compose up --build -d
    Write-Host "Waiting for ShadowGraph on http://localhost:8000 ..."
    for ($i = 0; $i -lt 90; $i++) {
        try { Invoke-RestMethod "http://localhost:8000/api/health" -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep 2 }
    }
    Start-Process "http://localhost:8000"
    Write-Host "Running. Stop with: docker compose down   (add -v to also delete the FalkorDB data)"
    exit 0
}

# ---- local mode: Python + Node on this machine, FalkorDB in the cloud
$envText = Get-Content ".env" -Raw
if ($envText -notmatch "(?m)^FALKORDB_URL=redis(s)?://(?!localhost)") {
    Write-Host "Set FALKORDB_URL in .env to your FalkorDB Cloud URL (redis://default:PASSWORD@HOST:PORT) first."
    exit 1
}
if (-not (Test-Path "backend\.venv")) {
    py -3.13 -m venv backend\.venv
}
& backend\.venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt
Push-Location frontend
if (-not (Test-Path "node_modules")) { npm ci --no-audit --no-fund }
npm run build
Pop-Location
$env:STATIC_DIR = Join-Path $Root "frontend\dist"
Push-Location backend
Start-Process "http://localhost:8000"
& .venv\Scripts\python.exe -m shadowgraph serve
Pop-Location
