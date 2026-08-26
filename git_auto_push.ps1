# HGTStudio auto-sync (PowerShell) - commit+push only when there are changes (SSH passwordless)
$repo = "D:\heygem_data\gpt_sovits"
$log  = Join-Path $repo "auto_push.log"
$git  = "C:\Users\lenovo\.workbuddy\vendor\PortableGit\mingw64\bin\git.exe"
Set-Location $repo
$st = & $git status --porcelain -- . ':(exclude).workbuddy' ':(exclude)auto_push.log'
if ($st) {
    # 只提交代码/配置: 排除 .workbuddy(代理记忆) 与 auto_push.log(日志)
    & $git add -A -- . ':(exclude).workbuddy' ':(exclude)auto_push.log'
    $msg = "daily backup: " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    & $git commit -q -m $msg
    & $git push -u origin main -q *>> $log
    Add-Content -Path $log -Value ("$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg -> pushed") -Encoding utf8
} else {
    Add-Content -Path $log -Value ("$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') no change, skip") -Encoding utf8
}
