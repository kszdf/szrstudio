@echo off
rem daily_hot_run.bat - called by scheduled task "DailyHot" (08:00 daily)
"D:\heygem\py310\Scripts\python.exe" "D:\heygem_data\gpt_sovits\daily_hot.py" --finance-top 4 --event-top 4 --per-source 30 >> "D:\heygem_data\runtime-logs\daily_hot_cron.log" 2>&1
