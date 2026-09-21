@echo off
rem ============================================
rem  公共资源门户 · 启动脚本（静默，无窗口）
rem  由计划任务 WK_Portal 以 SYSTEM 账户调用
rem ============================================
setlocal
set PORTAL=D:\web\portal
set PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe

rem 清掉可能干扰的代理变量（与项目其它模块一致）
set HTTP_PROXY=
set HTTPS_PROXY=
set ALL_PROXY=

cd /d "%PORTAL%"
"%PY%" "%PORTAL%\app.py"
endlocal
