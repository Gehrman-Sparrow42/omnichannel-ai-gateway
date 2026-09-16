@echo off
title Navitas Spa - WhatsApp Web Bridge
echo ===================================================
echo   Starting WhatsApp Web Bridge & QR Manager...
echo   Port: 3001 | Webhook: http://127.0.0.1:8000/webhook
echo ===================================================
cd /d "%~dp0bridge"
if not exist "node_modules" (
    echo [INFO] Installing Node.js dependencies...
    npm install
)
node index.js
pause
