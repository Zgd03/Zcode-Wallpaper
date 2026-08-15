@echo off
cd /d "%~dp0"

python --version
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python and add it to PATH.
  echo Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

rem Auto-start ZCode with the saved wallpaper (no console window)
start "" pythonw app\main.py --auto-start

