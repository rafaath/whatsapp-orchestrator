param([switch]$Remove, [string]$Distribution = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$taskName = 'WhatsApp Control Desk'
if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Output 'Removed the WhatsApp Control Desk logon task. Stored messages and settings remain.'
    exit
}
if ($Distribution.Contains('"') -or $Distribution.Contains("`r") -or $Distribution.Contains("`n")) { throw 'Invalid WSL distribution name.' }
$script = Join-Path $PSScriptRoot 'Start-ControlDesk.ps1'
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoLogo -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $script + '" -Distribution "' + $Distribution + '" -Foreground -NoBrowser')
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Starts the local WhatsApp bridge and control desk at sign-in.' -Force | Out-Null
Write-Output 'WhatsApp bridge and control desk will start when this Windows user signs in.'
