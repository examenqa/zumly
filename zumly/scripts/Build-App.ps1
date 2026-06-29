<#
.SYNOPSIS
    Builds the Zumly tray, editor, and export executables with PyInstaller.

.DESCRIPTION
    Creates/reuses a Python 3.13 virtual environment, installs dependencies,
    then produces the merged distribution at dist\zumly.
#>

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $RepoRoot

try {
    # ── Ensure virtual environment exists ────────────────────────
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "Creating Python 3.13 virtual environment..."
        py -3.13 -m venv .venv --without-pip
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Failed to create virtual environment. Is Python 3.13 x64 installed and visible to the py launcher?" -ForegroundColor Red
            exit 1
        }
    }

    # ── Ensure pip exists without relying on ensurepip temp files ─
    & .venv\Scripts\python.exe -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing pip into .venv..."
        py -3.13 -m pip --python .\.venv\Scripts\python.exe install pip
    }

    # ── Verify Python is x64 ────────────────────────────────────
    $archCheck = & .venv\Scripts\python.exe -c "import sys; print('ARM64' in sys.version)"
    if ($archCheck -eq "True") {
        Write-Host ""
        Write-Host "ARM64 native Python detected in .venv." -ForegroundColor Red
        Write-Host "  Please install Python 3.13 x64 and recreate the virtual environment:"
        Write-Host ""
        Write-Host "  1. winget install --id Python.Python.3.13 -e"
        Write-Host "  2. Delete .venv:  Remove-Item -Recurse -Force .venv"
        Write-Host "  3. Run Build-App.ps1 again."
        exit 1
    }

    Write-Host "Installing dependencies..."
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

    Write-Host "Clearing __pycache__..."
    Get-ChildItem -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force

    Write-Host ""
    Write-Host "Building Zumly..."
    & .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean zumly.spec

    Write-Host ""
    if ((Test-Path "dist\zumly\tray_app.exe") -and (Test-Path "dist\zumly\editor_app.exe") -and (Test-Path "dist\zumly\export_app.exe")) {
        Write-Host "Build succeeded: dist\zumly" -ForegroundColor Green
    } else {
        Write-Host "Build failed - check output above for errors." -ForegroundColor Red
    }
} finally {
    Pop-Location
}
