@echo off
cd /d "%~dp0"

python --version
if errorlevel 1 (
  echo [ERROR] Python not found. Please install Python and add it to PATH.
  echo Download: https://www.python.org/downloads/
  pause
  exit /b 1
)

rem Startup self-check: show import errors before launching the GUI
python -c "import sys; sys.path.insert(0,'app'); import main"
if errorlevel 1 (
  echo.
  echo [ERROR] Startup check failed. Run this to see details:
  echo     python app\main.py
  pause
  exit /b 1
)

rem Launch the GUI without a console window
start "" pythonw app\main.py

