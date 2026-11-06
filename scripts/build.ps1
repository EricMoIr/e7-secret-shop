# Builds dist\E7SecretShop.exe on Windows. From the project folder, in PowerShell:
#
#     powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#
# 1. Creates .venv-build (once), installs the app with PyInstaller, and prepares vendor\tesseract
#    (scripts\fetch_tesseract.py; needs 7-Zip).
# 2. Builds the self-test variant (same bundle, console, no admin manifest) and runs --self-test,
#    so a broken bundle fails here instead of after the UAC prompt.
# 3. Builds the real exe.
param([string]$Python = "python")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

# A running copy locks dist\E7SecretShop.exe, and the build would fail at the very end.
if (Get-Process E7SecretShop -ErrorAction SilentlyContinue) {
    throw "E7SecretShop is running: close every E7 Secret Shop window, then build again"
}

if (-not (Test-Path ".venv-build\Scripts\python.exe")) {
    Write-Host "Creating .venv-build"
    Invoke-Checked "Creating the build venv" { & $Python -m venv .venv-build }
}
$py = (Resolve-Path ".venv-build\Scripts\python.exe").Path
Invoke-Checked "Upgrading pip" { & $py -m pip install --quiet --upgrade pip }
Invoke-Checked "Installing dependencies" { & $py -m pip install --quiet -e ".[build]" }
# Tesseract to bundle. Up to date already when scripts/build-from-wsl.sh prepared it (stripped).
Invoke-Checked "Preparing Tesseract" { & $py scripts\fetch_tesseract.py }

Write-Host "Building the self-test exe"
$env:E7SHOP_SELFTEST_BUILD = "1"
try {
    Invoke-Checked "The self-test build" { & $py -m PyInstaller --noconfirm --clean --log-level WARN build.spec }
} finally {
    Remove-Item Env:E7SHOP_SELFTEST_BUILD
}

$report = Join-Path $env:TEMP "e7shop-selftest.txt"
Remove-Item $report -ErrorAction SilentlyContinue
& "dist\E7SecretShop-selftest.exe" --self-test $report
$code = $LASTEXITCODE
if (Test-Path $report) { Get-Content $report | ForEach-Object { Write-Host "  $_" } }
if ($code -ne 0) { throw "The self-test failed (exit code $code)" }
Remove-Item "dist\E7SecretShop-selftest.exe"

Write-Host "Building E7SecretShop.exe"
Invoke-Checked "The build" { & $py -m PyInstaller --noconfirm --log-level WARN build.spec }

$exe = Resolve-Path "dist\E7SecretShop.exe"
Write-Host ("Built {0} ({1:N1} MB)" -f $exe, ((Get-Item $exe).Length / 1MB))
