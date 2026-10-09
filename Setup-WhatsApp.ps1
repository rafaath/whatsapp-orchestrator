param([string]$Distribution = 'Ubuntu')

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Secure-Runtime.ps1')
$resolved = & wsl.exe --distribution $Distribution --exec wslpath -u $PSScriptRoot.Replace('\', '/')
if ($LASTEXITCODE -ne 0 -or -not $resolved) { throw 'Could not resolve this checkout in WSL. Check wsl --list --verbose.' }
$root = $resolved.Trim()
& wsl.exe --distribution $Distribution --exec bash "$root/scripts/setup.sh"
if ($LASTEXITCODE -ne 0) { throw 'Setup did not complete. See the prerequisite or build error above. No server was started.' }
Write-Output 'Setup complete. No servers started. Follow LOCAL_SETUP.md to link your own phone and register your own Codex connection.'
