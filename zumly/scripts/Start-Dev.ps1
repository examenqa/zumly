<#
.SYNOPSIS
    Dev setup and launch script for Zumly.

.DESCRIPTION
    Creates a virtual environment (if needed), installs dependencies,
    and launches the app.  Run once to set up, or any time to start.
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
            Write-Host ""
            Write-Host "Failed to create virtual environment." -ForegroundColor Red
            Write-Host "  Make sure Python 3.13 (x64) is installed and visible to the py launcher."
            Write-Host "  Install with: winget install --id Python.Python.3.13 -e"
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
        Write-Host "  3. Run Start-Dev.ps1 again."
        exit 1
    }

    # ── Install / update dependencies ────────────────────────────
    Write-Host "Installing dependencies..."
    & .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

    # ── Launch the app ───────────────────────────────────────────
    Write-Host ""
    Write-Host "Starting Zumly tray..."
    Write-Host ""
    & .venv\Scripts\python.exe tray_app.py
} finally {
    Pop-Location
}
