$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts/install.ps1 first' }
if ($env:SERIAL_HUB_HOME -or $env:SERIAL_HUB_API_PORT) {
    throw 'Custom SERIAL_HUB environment detected. Configure an explicit startup action rather than silently losing it.'
}
if (Get-ScheduledTask -TaskName 'SerialHubMCP' -ErrorAction SilentlyContinue) {
    throw 'SerialHubMCP already exists; inspect it before replacing it.'
}
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $python -Argument '-m serial_hub_mcp.cli serve' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'SerialHubMCP' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Loopback-only shared serial gateway for MCP and Telnet' | Out-Null
Write-Host 'Registered current-user logon task. It has not been started.'
Write-Host 'After stopping any manually running daemon: Start-ScheduledTask -TaskName SerialHubMCP'
