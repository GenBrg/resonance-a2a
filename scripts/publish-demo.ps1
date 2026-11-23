# Publish the static demo to GitHub Pages.
#
#   .\scripts\publish-demo.ps1
#
# The demo lives in `demo/` on main (source of truth). GitHub Pages serves from a
# `gh-pages` branch that contains only those three files at its root, which keeps the
# main tree tidy and avoids needing a CI workflow.
param(
    [string]$Remote = "origin",
    [string]$Branch = "gh-pages"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$demo = Join-Path $root "demo"
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("resonance-demo-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

if (-not (Test-Path (Join-Path $demo "index.html"))) {
    throw "demo/index.html 不存在"
}

Write-Host "==> 准备 $Branch（临时目录 $temp）" -ForegroundColor Cyan
New-Item -ItemType Directory -Path $temp | Out-Null
Copy-Item (Join-Path $demo "*") $temp -Recurse

Push-Location $temp
try {
    git init -q
    git checkout -q -b $Branch
    git add -A
    git -c user.name="GenBrg" -c user.email="GenBrg@users.noreply.github.com" commit -q -m "demo: publish static walkthrough"
    $remoteUrl = (git -C $root remote get-url $Remote)
    git remote add $Remote $remoteUrl
    Write-Host "==> 推送到 $remoteUrl ($Branch)" -ForegroundColor Cyan
    git push -f $Remote "${Branch}:${Branch}"
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}

Write-Host "==> 完成。若这是第一次发布，别忘了为仓库开启 Pages：" -ForegroundColor Yellow
Write-Host "    gh api --method POST repos/<owner>/<repo>/pages -f 'source[branch]=$Branch' -f 'source[path]=/'"
