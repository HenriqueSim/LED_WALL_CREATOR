@echo off
rem LED Wall Creator - run from the source folder on Windows.
rem First run: creates a private Python environment and installs the app's libraries (a few minutes).
setlocal
cd /d "%~dp0python"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up LED Wall Creator for the first time - this takes a few minutes...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo Python 3.10 or newer is needed. Get it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during the install. Then run this file again.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installing the libraries failed - check your internet connection and run this file again.
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" led_wall_motion_generator.py %*
