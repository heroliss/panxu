@echo off
setlocal
cd /d "%~dp0"
if exist "dist\PanXu\PanXu.exe" (
  start "" "dist\PanXu\PanXu.exe"
) else (
  python run.py
  if errorlevel 1 pause
)
