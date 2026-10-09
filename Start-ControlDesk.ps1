param([string]$Distribution = 'Ubuntu', [switch]$Foreground, [switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Secure-Runtime.ps1')
$windowsPath = $PSScriptRoot.Replace('\', '/')
$resolved = & wsl.exe --distribution $Distribution --exec wslpath -u $windowsPath
if ($LASTEXITCODE -ne 0 -or -not $resolved) { throw 'Could not resolve the WhatsApp folder in WSL.' }
$root = $resolved.Trim()
if ($Foreground) {
    & wsl.exe --distribution $Distribution --exec bash "$root/scripts/control.sh"
    exit $LASTEXITCODE
}
$running = $false
try { $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/' -TimeoutSec 2; $running = $true } catch { }
if (-not $running) {
    $launch = Join-Path $PSScriptRoot 'Start-ControlDesk.ps1'
    $arguments = @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $launch + '"'),'-Distribution',$Distribution,'-Foreground','-NoBrowser')
    Start-Process powershell.exe -ArgumentList $arguments -WindowStyle Minimized
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try { $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8766/' -TimeoutSec 1; $running = $true; break } catch { }
    }
}
if (-not $running) { throw 'The desk did not start. Check the service window or runtime\desk-service.log. Stop an older bridge window before retrying.' }
if (-not $NoBrowser) {
    $token = (Get-Content -Raw (Join-Path $PSScriptRoot 'runtime\control-token')).Trim()
    Start-Process ('http://127.0.0.1:8766/#token=' + $token)
}
Write-Output 'WhatsApp Control Desk is running at http://127.0.0.1:8766. The service window can stay minimized.'
