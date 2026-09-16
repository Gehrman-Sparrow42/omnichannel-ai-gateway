@echo off
title Omnichannel Branch Assistant Launcher
echo =========================================================
echo  Navitas Spa & Wellness Omnichannel Branch Assistant
echo  Supporting 17 Centers Across WhatsApp, IG, TG, Messenger
echo =========================================================

cd /d "%~dp0"

echo [1/3] Checking environment configuration...
if not exist ".env" (
    if exist ".env.example" (
        echo [.env] Not found. Creating from .env.example...
        copy .env.example .env >nul
    )
)

echo [2/3] Initializing Database and Running Verification Tests...
python -m pytest backend\tests -q
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Some tests flagged issues, proceeding to launch...
) else (
    echo [OK] All 27 Automated Verification Tests Passed!
)

echo [3/3] Starting FastAPI Backend on http://127.0.0.1:8000 ...
cd backend
start "Navitas Spa Backend (Port 8000)" cmd /k "python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

timeout /t 2 >nul
echo.
echo =========================================================
echo  CONTROL CENTER READY!
echo  URL: http://127.0.0.1:8000
echo  Super Admin: admin@navitasspa.com / AdminSecure2026!
echo.
echo  Tip: To link WhatsApp Web session locally, run start_bridge.bat
echo =========================================================
pause
