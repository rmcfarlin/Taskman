@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-taskman.ps1" %*
exit /b %errorlevel%
