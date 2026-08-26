@echo off
echo ============================================================
echo  Open LAN access for workbench (RUN AS ADMIN)
echo  Adds Windows Firewall inbound rules:
echo    TCP 8385  workbench (studio)  -> http://192.168.4.103:8385
echo    TCP 8500  pipeline API        -> for LAN scripts/apps
echo ============================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo [1/2] Add firewall rules ...
netsh advfirewall firewall delete rule name="HGT Studio 8385" >nul 2>&1
netsh advfirewall firewall add rule name="HGT Studio 8385" dir=in action=allow protocol=TCP localport=8385
netsh advfirewall firewall delete rule name="HGT Pipeline 8500" >nul 2>&1
netsh advfirewall firewall add rule name="HGT Pipeline 8500" dir=in action=allow protocol=TCP localport=8500

echo [2/2] Verify rules ...
netsh advfirewall firewall show rule name="HGT Studio 8385" | findstr /i "Rule Name Enabled Action"
netsh advfirewall firewall show rule name="HGT Pipeline 8500" | findstr /i "Rule Name Enabled Action"

echo.
echo ============================================================
echo  Done. LAN colleagues can now open:
echo    http://192.168.4.103:8385
echo  (use the account you create via account_admin.py)
echo ============================================================
pause
