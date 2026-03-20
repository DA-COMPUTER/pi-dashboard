@echo off
REM ═══════════════════════════════════════════════════════════════════════════
REM  build_windows.bat  —  Builds devboard-windows.exe
REM  Run this ON a Windows machine (or Windows GitHub Actions runner).
REM
REM  Requirements:
REM    pip install pyinstaller
REM    (optional) install UPX from https://upx.github.io/ and put on PATH
REM
REM  Output:  dist\devboard-windows.exe
REM ═══════════════════════════════════════════════════════════════════════════

setlocal enabledelayedexpansion

echo.
echo ───────────────────────────────────────────────────────────
echo   DevBoard — Windows Build
echo ───────────────────────────────────────────────────────────
echo.

REM ── 1. Check Python ────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found on PATH.
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo   Python : %%v

REM ── 2. Install / upgrade PyInstaller ───────────────────────
echo   Installing PyInstaller…
pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo  ERROR: pip install pyinstaller failed.
    exit /b 1
)

REM ── 3. Install app dependencies ────────────────────────────
echo   Installing app dependencies…
pip install --quiet --upgrade flask psutil pywebview
if errorlevel 1 (
    echo  WARNING: Some dependencies failed to install.
    echo           The bundle may still work if they are already present.
)

REM ── 4. Clean previous build ────────────────────────────────
if exist "dist\devboard.exe"         del /f /q "dist\devboard.exe"
if exist "dist\devboard-windows.exe" del /f /q "dist\devboard-windows.exe"
if exist "build"                         rmdir /s /q build

REM ── 5. Run PyInstaller ─────────────────────────────────────
echo   Running PyInstaller…
pyinstaller devboard.spec --noconfirm
if errorlevel 1 (
    echo  ERROR: PyInstaller failed.
    exit /b 1
)

REM ── 6. Rename output ───────────────────────────────────────
if not exist "dist\devboard.exe" (
    echo  ERROR: Expected dist\devboard.exe not found.
    exit /b 1
)
rename "dist\devboard.exe" "devboard-windows.exe"

REM ── 7. Print size & done ───────────────────────────────────
for %%f in ("dist\devboard-windows.exe") do (
    set /a size_mb=%%~zf / 1048576
    echo.
    echo   ✓  dist\devboard-windows.exe  (!size_mb! MB^)
)
echo.
echo ───────────────────────────────────────────────────────────
echo   Build complete.  Upload dist\devboard-windows.exe
echo   to the GitHub Release.
echo ───────────────────────────────────────────────────────────
echo.
endlocal
