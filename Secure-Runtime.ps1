$ErrorActionPreference = 'Stop'
$runtime = Join-Path $PSScriptRoot 'runtime'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $runtime /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not secure the WhatsApp runtime folder. Device linking has not been started.'
}
# Removing inheritance recursively can leave files with an empty ACL. Secure the
# parent first, then make descendants inherit only its owner and SYSTEM grants.
if (Get-ChildItem -LiteralPath $runtime -Force | Select-Object -First 1) {
    & icacls.exe (Join-Path $runtime '*') /reset /T /C | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not restore secured runtime inheritance. Stop the services and run this script again.'
    }
}
