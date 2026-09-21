# start_platform.ps1 - start platform stack via scheduled task (watchdog brings up app+tunnel)
Start-ScheduledTask -TaskName "WK_AutoTaskPlatform"
Write-Host "Started. Watchdog will bring up platform (8766) and cloudflared."
Write-Host "Health check: http://127.0.0.1:8766/health"
