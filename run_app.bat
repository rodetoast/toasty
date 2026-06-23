@echo off
setlocal
cd /d "%~dp0"

set "PYTHON_CMD="

where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
        set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python313\python.exe"
    )
)

if not defined PYTHON_CMD (
    echo Python was not found.
    echo Install Python 3.9 or newer, then run this file again.
    pause
    exit /b 1
)

set "VENV_PY=venv\Scripts\python.exe"

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; print(sys.version)" >nul 2>nul
    if errorlevel 1 (
        echo Existing venv is broken. Recreating it...
        %PYTHON_CMD% -m venv --clear venv
    )
) else (
    echo Creating venv...
    %PYTHON_CMD% -m venv venv
)

if errorlevel 1 (
    echo Failed to create the virtual environment.
    pause
    exit /b 1
)

"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    pause
    exit /b 1
)

"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    "%VENV_PY%" -c "import PyQt6, wordninja, thefuzz, Levenshtein, main; print('run_app check ok')"
    exit /b %errorlevel%
)

"%VENV_PY%" main.py
if errorlevel 1 (
    echo.
    echo The app exited with an error. Check aseprite_tag_master_error.log if it was created.
    pause
    exit /b 1
)

endlocal
