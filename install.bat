@echo off
REM bcagent installer for Windows - double-click or run from cmd
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 pause
