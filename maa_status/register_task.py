# -*- coding: utf-8 -*-
"""注册 MAA 进度看板的常驻计划任务（MAA-StatusBoard）。

  * 触发器 1：登录时启动（开机即拉起）
  * 触发器 2：每 30 分钟重复（进程挂了能自愈；脚本内 msvcrt 独占锁保证单实例）
  * 静默：pythonw.exe（无控制台窗口）
  * ExecutionTimeLimit=PT0S：不限制运行时长

注意：注册计划任务必须用带 pywin32 的解释器（venv Scripts\\python.exe）。
"""
import datetime
import os
import sys
import time

import win32com.client

HERE = os.path.dirname(os.path.abspath(__file__))
PYW = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
TASK = "MAA-StatusBoard"
START = datetime.datetime.now() + datetime.timedelta(minutes=1)

xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>MAA 夜间进度看板（本机 127.0.0.1:8791），登录自启 + 每 30 分钟自愈检查</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>Administrator</UserId>
    </LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{START.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT30M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>Administrator</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{PYW}</Command>
      <Arguments>"{os.path.join(HERE, 'server.py')}"</Arguments>
      <WorkingDirectory>{HERE}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

sched = win32com.client.Dispatch("Schedule.Service")
sched.Connect()
root = sched.GetFolder("\\")
try:
    root.DeleteTask(TASK, 0)
    print("deleted old:", TASK)
except Exception:
    pass
root.RegisterTask(TASK, xml, 6, None, None, 3)
print("registered:", TASK)

t = root.GetTask(TASK)
print("enabled:", t.Enabled, "state:", t.State)
try:
    t.Run(None)
    print("started via IRegisteredTask.Run()")
except Exception as e:
    print("run failed:", e)

time.sleep(4)
import socket
s = socket.socket()
alive = s.connect_ex(("127.0.0.1", 8791)) == 0
s.close()
print("port 8791 listening:", alive)
