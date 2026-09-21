@echo off
rem ============================================
rem  自动任务平台 · 开机静默启动（经看护进程统一管理）
rem  平台与 cloudflared 均由 health_manager.py 拉起并看护；
rem  本脚本可重复执行（看护进程有互斥体，重复运行自动退出）。
rem ============================================
setlocal
set WK=D:\web
set PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe

rem 清掉可能干扰 cloudflared 的代理变量
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=

echo 启动看护进程（自动拉起平台 8766 + Cloudflare 隧道）...
start "" /min "%PY%" "%WK%\health_manager.py"

echo 完成。网站: https://order.jiangjiangze.icu
endlocal
