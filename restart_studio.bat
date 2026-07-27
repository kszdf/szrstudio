@echo off
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":8385" ^| findstr /C:"LISTENING"') do taskkill /PID %%a /F
timeout /t 2 /nobreak >nul
start "" "C:\Users\lenovo\.workbuddy\binaries\python\versions\3.13.12\python.exe" "D:\heygem_data\gpt_sovits\rewrite_studio.py"
timeout /t 3 /nobreak >nul
start http://localhost:8385
