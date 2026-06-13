# Publish CFB Viewer to GitHub + Render
# Run from C:\Users\ender\cfb-viewer after: gh auth login

$ErrorActionPreference = "Stop"
$git = "C:\Program Files\Git\bin\git.exe"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
$repoName = "cfb-viewer"

Set-Location $PSScriptRoot

& $gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Not logged into GitHub. Run: gh auth login" -ForegroundColor Yellow
    exit 1
}

$exists = & $gh repo view $repoName 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating public GitHub repo: $repoName"
    & $gh repo create $repoName --public --source=. --remote=origin --push
} else {
    Write-Host "Repo exists; pushing to origin main"
    & $git push -u origin main
}

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$remoteUrl = (& $git remote get-url origin).Trim()
Write-Host ""
Write-Host "GitHub repo ready: $remoteUrl" -ForegroundColor Green
Write-Host ""
Write-Host "Next: deploy on Render" -ForegroundColor Cyan
Write-Host "  1. https://dashboard.render.com/blueprints"
Write-Host "  2. New Blueprint Instance -> connect $repoName"
Write-Host "  3. Apply (uses render.yaml in repo)"
Write-Host "  4. Open the service URL when deploy finishes"
