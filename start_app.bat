@echo off
REM Start SlitProjektHub with virtual environment

setlocal enabledelayedexpansion

REM Get script directory
set SCRIPT_DIR=%~dp0

REM Change to project directory
cd /d "%SCRIPT_DIR%"

REM Check if virtual environment exists
if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM Install/update requirements
echo Installing dependencies...
pip install -q -r requirements.txt

REM Start Backend (FastAPI) in separate window
echo.
echo ========================================
echo Starting Backend (FastAPI)...
echo ========================================
start "Backend - FastAPI" cmd /k "cd /d "%SCRIPT_DIR%backend" && call "%SCRIPT_DIR%.venv\Scripts\activate.bat" && python main.py"

REM Wait for backend to start
timeout /t 3 /nobreak >nul

REM Start Streamlit app
echo.
echo ========================================
echo Starting Frontend (Streamlit)...
echo ========================================
echo.
echo Open your browser at: http://localhost:8501
echo Backend API: http://localhost:8000/docs
echo.
echo Close this window to stop the frontend
echo (Close the "Backend" window separately)
echo.

streamlit run app/streamlit_app.py

REM Cleanup on exit
echo.
echo Frontend stopped.
echo Remember to close the Backend window!
pause
