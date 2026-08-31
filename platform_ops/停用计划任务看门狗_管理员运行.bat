@echo off
echo ============================================================
echo  Disable scheduled-task watchdog, KEEP nssm watchdog
echo  (run as admin - auto-elevates if not)
echo  Keeps:   nssm service  HEYGEMWatchdog (heygem_watchdog.py,
echo            works 7:00-22:00, respects 22:00 maintenance)
echo  Disable: scheduled task HEYGEMWatchdog (heygem-watchdog.ps1,
echo            every 5 min, no maintenance window)
echo ============================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/2] Disable scheduled task HEYGEMWatchdog ...
schtasks /change /tn HEYGEMWatchdog /disable

echo [2/2] Verify ...
echo   -- scheduled task (should be Disabled) --
schtasks /query /tn HEYGEMWatchdog /v /fo list | findstr /i "Status"
echo   -- nssm service (should be RUNNING) --
sc query HEYGEMWatchdog | findstr /i "STATE"

echo.
echo ============================================================
echo  Done. If task Status = Disabled and service STATE = RUNNING,
echo  consolidation succeeded. If you ever want it back:
echo    schtasks /change /tn HEYGEMWatchdog /enable
echo ============================================================
pause
