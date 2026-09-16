@echo off
title Stop Omnichannel Assistant
echo Stopping Omnichannel Assistant services...

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo Terminating backend process PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":3001" ^| findstr "LISTENING"') do (
    echo Terminating bridge process PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo Services stopped cleanly.
pause
