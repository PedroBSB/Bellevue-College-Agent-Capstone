@echo off
rem Remove bcagent.   uninstall.bat        -> bcagent only (keeps settings, Ollama and models)
rem                   uninstall.bat --all  -> everything: bcagent, settings, MCP servers, models and Ollama
if /I "%~1"=="--all" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -UninstallAll
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
)
