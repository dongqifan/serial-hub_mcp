@echo off
setlocal DisableDelayedExpansion
set "pythonw=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%pythonw%" (
    echo Serial Hub is not installed. Run this command in PowerShell first:
    echo powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1"
    pause
    exit /b 1
)
start "" "%pythonw%" -m serial_hub_mcp.cli gui
