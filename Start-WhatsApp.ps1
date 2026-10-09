param([string]$Distribution = 'Ubuntu')

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Secure-Runtime.ps1')
$windowsPath = $PSScriptRoot.Replace('\', '/')
$resolved = & wsl.exe --distribution $Distribution --exec wslpath -u $windowsPath
if ($LASTEXITCODE -ne 0 -or -not $resolved) { throw 'Could not resolve the WhatsApp folder in WSL.' }
$root = $resolved.Trim()
& wsl.exe --distribution $Distribution --exec bash "$root/scripts/bridge.sh"
exit $LASTEXITCODE
