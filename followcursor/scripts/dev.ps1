<#
.SYNOPSIS
    Dev setup and launch script for FollowCursor.

.DESCRIPTION
    Creates a virtual environment (if needed), installs dependencies,
    and launches the app.  Run once to set up, or any time to start.
#>

$ErrorActionPreference = "Stop"
$FollowCursorRoot = Split-Path -Parent $PSScriptRoot
Push-Location $FollowCursorRoot

try {
    # ── Ensure virtual environment exists ────────────────────────
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "Creating virtual environment..."
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "Failed to create virtual environment." -ForegroundColor Red
            Write-Host "  Make sure Python 3.10+ (x64) is installed and on your PATH."
            Write-Host "  Download from https://www.python.org/downloads/"
            Write-Host "  NOTE: On ARM64 Windows, install the x64 edition."
            exit 1
        }
    }

    # ── Verify Python is x64 ────────────────────────────────────
    $archCheck = & .venv\Scripts\python.exe -c "import sys; print('ARM64' in sys.version)"
    if ($archCheck -eq "True") {
        Write-Host ""
        Write-Host "ARM64 native Python detected in .venv." -ForegroundColor Red
        Write-Host "  Several dependencies (OpenCV, dxcam) have no ARM64 wheels."
        Write-Host "  Please install Python x64 and recreate the virtual environment:"
        Write-Host ""
        Write-Host '  1. Install "Windows installer (64-bit)" from https://www.python.org/downloads/'
        Write-Host "  2. Delete .venv:  Remove-Item -Recurse -Force .venv"
        Write-Host '  3. Recreate:      & "C:\path\to\python-x64\python.exe" -m venv .venv'
        Write-Host "  4. Run dev.ps1 again."
        exit 1
    }

    # ── Install / update dependencies ────────────────────────────
    Write-Host "Installing dependencies..."
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

    # ── Launch the app ───────────────────────────────────────────
    Write-Host ""
    Write-Host "Starting FollowCursor..."
    Write-Host ""
    & .venv\Scripts\python.exe main.py
} finally {
    Pop-Location
}
