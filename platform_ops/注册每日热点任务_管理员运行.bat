@echo off
echo ============================================================
echo  Register daily-hot scheduled task (RUN AS ADMIN)
echo  Runs daily_hot.py at 08:00 every day -> generates
echo  finance/event hot topics + viral plans into daily_hot.json
echo ============================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/2] Create scheduled task "DailyHot" (daily 08:00) ...
schtasks /create /tn "DailyHot" /tr "D:\heygem_data\gpt_sovits\daily_hot_run.bat" /sc daily /st 08:00 /f

echo [2/2] Verify ...
schtasks /query /tn DailyHot /v /fo list | findstr /i "Status Task To Run Next Run Time"

echo.
echo ============================================================
echo  Done. Workbench step 1 topic -> [Daily Hot] button reads the result.
echo  Manual run anytime: D:\heygem\py310\Scripts\python.exe
echo    D:\heygem_data\gpt_sovits\daily_hot.py
echo  Remove task: schtasks /delete /tn DailyHot /f
echo ============================================================
pause
