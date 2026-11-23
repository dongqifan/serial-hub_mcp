param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe'))) {
    if ($Python -eq 'py') { & $Python -3 -m venv (Join-Path $root '.venv') }
    else { & $Python -m venv (Join-Path $root '.venv') }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create virtual environment' }
}
$exe = Join-Path $root '.venv\Scripts\python.exe'
& $exe -m pip install -e "$root[test]"
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
Push-Location $root
try {
    & $exe -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed; installation is not verified' }
} finally { Pop-Location }
Write-Host "Installed. To manage ports, double-click '$root\Serial Hub.cmd'."
Write-Host "Console alternative: & '$root\.venv\Scripts\serial-hub.exe' gui"
Write-Host "MCP command: $root\.venv\Scripts\python.exe -m serial_hub_mcp.mcp_server"
Write-Host 'OpenCode configuration and startup tasks are NOT modified by this installer.'
