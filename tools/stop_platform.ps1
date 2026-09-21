# stop_platform.ps1 - graceful stop of platform/watchdog/tunnel (maintenance)
Stop-ScheduledTask -TaskName "WK_AutoTaskPlatform" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Get-CimInstance Win32_Process | Where-Object { $_.Name -in @("pythonw.exe","python.exe") -and $_.CommandLine -match "order_platform|health_manager" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Stopped: scheduled task, platform, watchdog, tunnel."
