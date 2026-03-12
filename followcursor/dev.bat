@echo off
REM ── FollowCursor dev setup ────────────────────────────────────
REM Creates a virtual environment, installs dependencies, and
REM launches the app. Run this once to set up, or any time to
REM start the app.
REM Usage: dev.bat

cd /d "%~dp0"

REM ── Ensure virtual environment exists ─────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ✗ Failed to create virtual environment.
        echo   Make sure Python 3.10+ ^(x64^) is installed and on your PATH.
        echo   Download from https://www.python.org/downloads/
        echo   NOTE: On ARM64 Windows, install the "Windows installer ^(64-bit^)"
        echo         ^(x86-64^), NOT the ARM64 version. x64 runs via emulation
        echo         and is required for OpenCV and other binary packages.
        exit /b 1
    )
)

REM ── Verify Python is x64 (ARM64 native has no binary wheels) ─
.venv\Scripts\python.exe -c "import sys; exit(1 if 'ARM64' in sys.version else 0)" 2>nul
if errorlevel 1 (
    echo.
    echo ✗ ARM64 native Python detected in .venv.
    echo   Several dependencies ^(OpenCV, dxcam^) have no ARM64 wheels.
    echo   Please install Python x64 and recreate the virtual environment:
    echo.
    echo   1. Install "Windows installer ^(64-bit^)" from https://www.python.org/downloads/
    echo   2. Delete .venv:  rmdir /s /q .venv
    echo   3. Recreate:      "C:\path\to\python-x64\python.exe" -m venv .venv
    echo   4. Run dev.bat again.
    echo.
    exit /b 1
)

REM ── Install / update dependencies ─────────────────────────────
echo Installing dependencies...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

REM ── Launch the app ────────────────────────────────────────────
echo.
echo Starting FollowCursor...
echo.
.venv\Scripts\python.exe main.py
