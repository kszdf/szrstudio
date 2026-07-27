@echo off
:: 一键以管理员权限重启 NSSM 服务 HGTStudio（慧根堂短视频工作台 / 端口 8385）
:: 用法：双击本文件 -> 弹出 UAC 点"是" -> 等待重启完成 -> 按任意键关闭 -> 浏览器 Ctrl+Shift+R 强刷

net session >nul 2>&1
if %errorLevel% equ 0 goto :admin

echo ============================================
echo   需要管理员权限才能重启 8385 服务
echo   即将弹出"用户账户控制"框，请点【是】
echo ============================================
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
echo.
echo [提示] 如果上面没有出现"服务已重启"的窗口，说明你取消了 UAC 提权。
echo         请重新双击本文件，并在弹出的用户账户控制框点【是】。
echo.
pause
exit /b

:admin
echo 正在重启 HGTStudio 服务（慧根堂短视频工作台 / 端口 8385）...
net stop HGTStudio
net start HGTStudio
echo.
echo [OK] 服务已重启。请到浏览器打开 http://localhost:8385 并按 Ctrl+Shift+R 强刷。
echo       （若想确认，可在 8385 页面看二创界面是否多出"目标时长(秒)"输入框）
echo.
pause
