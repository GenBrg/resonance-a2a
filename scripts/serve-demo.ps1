# Resonance demo launcher (Windows / PowerShell)
#
#   .\scripts\serve-demo.ps1                          # live engine if a key is provided
#   .\scripts\serve-demo.ps1 -Offline                 # deterministic engine, no network
#   .\scripts\serve-demo.ps1 -ApiKey "sk-..."          # pass the gateway key explicitly
#   $env:RESONANCE_LLM_API_KEY="sk-..." ; .\scripts\serve-demo.ps1
#
# Nothing is written to disk: the key stays in this process's environment.
param(
    [switch]$Offline,
    [int]$Port = 8000,
    [string]$ApiKey = $env:RESONANCE_LLM_API_KEY,
    [string]$BaseUrl = $env:RESONANCE_LLM_BASE_URL,
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "未找到虚拟环境，先执行：" -ForegroundColor Yellow
    Write-Host "  cd backend; uv venv .venv --python 3.10; uv pip install -e '.[dev]'" -ForegroundColor Yellow
    exit 1
}

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONPATH = $backend

if ($Offline) {
    $env:LLM_MODE = "deterministic"
    Write-Host "引擎：deterministic（零成本、无网络）" -ForegroundColor Cyan
} else {
    # Optional: read the key from an env file (KEY=value lines) instead of -ApiKey
    if (-not $ApiKey -and $EnvFile -and (Test-Path $EnvFile)) {
        Get-Content $EnvFile | ForEach-Object {
            if ($_ -match "^\s*(?:RESONANCE_LLM_API_KEY|LLM_API_KEY|OPENAI_API_KEY)\s*=\s*(.+)$") {
                $ApiKey = $matches[1].Trim().Trim('"').Trim("'")
            }
        }
    }
    if ($ApiKey) {
        $env:LLM_API_KEY = $ApiKey
        if ($BaseUrl) { $env:LLM_BASE_URL = $BaseUrl }
        $env:LLM_MODE = "live"
        Write-Host "引擎：live（key 来自参数或环境变量，不落盘）" -ForegroundColor Green
    } else {
        Write-Host "没有提供 LLM key，回退到 deterministic 引擎（功能完全一致，只是对话来自模板）" -ForegroundColor Yellow
        $env:LLM_MODE = "deterministic"
    }
}

Push-Location $backend
try {
    & $python -m app.cli init | Out-Null
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
