@echo off
echo =========================================
echo  TruthSeeker — First-time setup
echo =========================================
echo.

:: Check if pip is available
python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python / pip not found. Please install Python 3.10+ first.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo Launching TruthSeeker...
python truthseeker.py
