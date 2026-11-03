@echo off
setlocal
cd /d "%~dp0"
rem Use the packaged 1.x build only if it exists (older 0.x builds in dist\PanXu lack this file).
if exist "dist\PanXu\_internal\web\page-junk.js" (
  start "" "dist\PanXu\PanXu.exe"
) else (
  python run.py
  if errorlevel 1 pause
)
