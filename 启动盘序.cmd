@echo off
setlocal
cd /d "%~dp0"
rem Use the packaged 1.x build only if it exists (older 0.x builds in dist\PanXu lack this file).
if exist "dist\PanXu\_internal\web\page-junk.js" (
  start "" "dist\PanXu\PanXu.exe"
  exit /b 0
)
rem From source: pythonw (next to the python on PATH) runs without a console window; errors are
rem shown in a message box and written to %LOCALAPPDATA%\PanXu\panxu.log.
set "PYEXE="
set "PYW="
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE for %%d in ("%PYEXE%") do if exist "%%~dpdpythonw.exe" set "PYW=%%~dpdpythonw.exe"
if defined PYW (
  start "" "%PYW%" run.py
  exit /b 0
)
rem No pythonw: run in this window, and keep it open on errors.
python run.py
if errorlevel 1 pause
