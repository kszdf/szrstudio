# HGTStudio auto-sync (PowerShell) - commit+push only when there are changes (SSH passwordless)
$repo = "D:\heygem_data\gpt_sovits"
$log  = Join-Path $repo "auto_push.log"
$git  = "C:\Users\lenovo\.workbuddy\vendor\PortableGit\mingw64\bin\git.exe"
Set-Location $repo
$st = & $git status --porcelain
if ($st) {
    & $git add -A
    $msg = "auto sync: " + (Get-Date -Format "yyyy-MM-dd HH:mm")
    & $git commit -q -m $msg
    & $git push -u origin main -q *>> $log
    Add-Content $log ("$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg -> pushed")
} else {
    Add-Content $log ("$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') no change, skip")
}
