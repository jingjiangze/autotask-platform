@echo off
rem stage-cloud-35 — §113 本机 Local Executor 开机自启（Task Scheduler 调用）。
rem 停止自启：schtasks /Delete /TN "AutotaskLocalExecutor" /F
setlocal
cd /d D:\web\_wt_stagecloud\cloudflare\executor
for /f "usebackq tokens=1,* delims==" %%A in ("..\deploy-secrets.txt") do (
  if "%%A"=="EXECUTOR_TOKEN_LOCAL" set "EXECUTOR_TOKEN=%%B"
)
set "CENTRAL_URL=https://executor.jiangjiangze.icu"
set "EXECUTOR_ID=exec-local-01"
set "EXECUTION_PATH=local"
set "EXECUTOR_CAPABILITIES=chaoxing,zhs"
set "PYTHONIOENCODING=utf-8"
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -u agent\main.py --runner chaoxing >> ..\..\logs_local_executor.txt 2>&1
