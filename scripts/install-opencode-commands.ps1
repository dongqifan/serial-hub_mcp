param([string]$ConfigDirectory = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $ConfigDirectory) {
    if ($env:XDG_CONFIG_HOME) { $ConfigDirectory = Join-Path $env:XDG_CONFIG_HOME 'opencode' }
    else { $ConfigDirectory = Join-Path $HOME '.config\opencode' }
}
$destination = Join-Path $ConfigDirectory 'commands'
$source = Join-Path $root 'opencode\commands'
$files = Get-ChildItem -LiteralPath $source -Filter 'serial-*.md' -File
# Check every target before copying any file. Never overwrite customized commands.
foreach ($file in $files) {
    $target = Join-Path $destination $file.Name
    if ((Test-Path -LiteralPath $target) -and
        ((Get-FileHash -LiteralPath $target).Hash -ne (Get-FileHash -LiteralPath $file.FullName).Hash)) {
        throw "Command already exists with different contents: $target. Back up or rename it first."
    }
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
foreach ($file in $files) {
    Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $destination $file.Name)
}
Write-Host "Installed global serial commands in $destination"
Write-Host 'Commands reload automatically. Use /reload if needed. MCP serial_hub must already be configured.'
