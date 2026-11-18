@echo off
setlocal
cd /d "%~dp0"
rem From source first (docs/DEVELOPMENT.md): pythonw runs the current code without a console window;
rem errors are shown in a message box and written to %LOCALAPPDATA%\PanXu\panxu.log.
set "PYEXE="
set "PYW="
for /f "delims=" %%i in ('python -X utf8=0 -c "import sys, webview; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
if defined PYEXE if exist "%PYEXE%" (
  for %%d in ("%PYEXE%") do if exist "%%~dpdpythonw.exe" set "PYW=%%~dpdpythonw.exe"
  if defined PYW (
    start "" "%PYW%" run.py %*
    exit /b 0
  )
  python run.py %*
  if errorlevel 1 pause
  exit /b %errorlevel%
)
rem Fallback when Python or pywebview is not installed: use the packaged build if present.
if exist "dist\PanXu\_internal\web\page-junk.js" (
  start "" "dist\PanXu\PanXu.exe" %*
  exit /b 0
)
python run.py %*
if errorlevel 1 pause
