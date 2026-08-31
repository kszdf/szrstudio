@echo off
echo ============================================================
echo  Platform one-click restart (RUN AS ADMIN) - 8500 service
echo  Writes env vars (parallel render/TTS + proxy), sets log
echo  redirection, restarts HGTCommercial8500, verifies health.
echo ============================================================
echo.

echo [1/6] Write HGTCommercial8500 env vars (MOTION_WORKERS=12 ...)
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTCommercial8500\Parameters" /v AppEnvironmentExtra /t REG_MULTI_SZ /d "MOTION_WORKERS=12\0MOTION_TTS_WORKERS=4\0HTTP_PROXY=http://127.0.0.1:7897\0HTTPS_PROXY=http://127.0.0.1:7897" /f

echo [2/6] Configure log redirection (runtime-logs\8500-server.log, 10MB rotate)
if not exist "D:\heygem_data\runtime-logs" mkdir "D:\heygem_data\runtime-logs"
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTCommercial8500\Parameters" /v AppStdout /t REG_EXPAND_SZ /d "D:\heygem_data\runtime-logs\8500-server.log" /f
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTCommercial8500\Parameters" /v AppStderr /t REG_EXPAND_SZ /d "D:\heygem_data\runtime-logs\8500-server.log" /f
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTCommercial8500\Parameters" /v AppRotateFiles /t REG_DWORD /d 1 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTCommercial8500\Parameters" /v AppRotateBytes /t REG_DWORD /d 10485760 /f

echo [3/6] Restart HGTCommercial8500 ...
net stop HGTCommercial8500 >nul 2>&1
ping -n 3 127.0.0.1 >nul
net start HGTCommercial8500
ping -n 13 127.0.0.1 >nul

echo [3b] Restart HGTStudio (8385 workbench) to load motion entry ...
reg add "HKLM\SYSTEM\CurrentControlSet\Services\HGTStudio\Parameters" /v AppEnvironmentExtra /t REG_MULTI_SZ /d "AUTH_ENABLED=1" /f
net stop HGTStudio >nul 2>&1
ping -n 3 127.0.0.1 >nul
net start HGTStudio
ping -n 9 127.0.0.1 >nul

echo [4/6] Verify local and cloud health ...
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8500/health' -TimeoutSec 10 -UseBasicParsing; Write-Host ('  local 8500: HTTP ' + $r.StatusCode + ' ' + $r.Content) } catch { Write-Host ('  local 8500 FAIL: ' + $_.Exception.Message) }"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8385/' -TimeoutSec 10 -UseBasicParsing; Write-Host ('  local 8385 studio: HTTP ' + $r.StatusCode) } catch { Write-Host ('  local 8385 FAIL: ' + $_.Exception.Message) }"
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://zmgen.cn:8500/health' -TimeoutSec 15 -UseBasicParsing; Write-Host ('  cloud zmgen.cn:8500: HTTP ' + $r.StatusCode + ' ' + $r.Content) } catch { Write-Host ('  cloud FAIL: ' + $_.Exception.Message) }"

echo [5/6] Verify Docker containers ...
docker ps --format "  {{.Names}} | {{.Status}}" | findstr /i "heygem hgt-commercial"

echo [6/6] Verify frp tunnel ...
sc query frpc | findstr /i "RUNNING" >nul && echo   frpc: RUNNING || echo   frpc: NOT RUNNING!

echo.
echo ============================================================
echo  Done. 8500 now runs latest code + 12 parallel render workers
echo  + 4 parallel TTS + proxy injection. Log: runtime-logs\8500-server.log
echo  Note: if scheduled-task watchdog and nssm watchdog both exist,
echo    keep ONE:  schtasks /change /tn HEYGEMWatchdog /disable
echo ============================================================
pause
