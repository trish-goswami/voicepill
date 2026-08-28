# Run this ONCE from an ADMIN PowerShell if ctrl+space still reaches Warp.
#
# A low-level keyboard hook can only swallow keys from apps running at or below
# its own privilege. Warp getting ctrl+space despite suppress=True means it wins
# on privilege, so VoicePill has to start elevated. A scheduled task with
# RunLevel Highest does that at logon with no UAC prompt.
#
#   powershell -ExecutionPolicy Bypass -File install-elevated-autostart.ps1

$ErrorActionPreference = 'Stop'

if (-not ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Not elevated. Right-click PowerShell -> Run as administrator, then re-run this." -ForegroundColor Red
    exit 1
}

$me  = "$env:USERDOMAIN\$env:USERNAME"
$pyw = (Get-Command pythonw).Source
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path

$action = New-ScheduledTaskAction -Execute $pyw -Argument 'voicepill.py' -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName 'VoicePill' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# the Startup shortcut would start a second, unelevated copy; the app's mutex
# would make one of them exit, but which one is a coin toss - so remove it
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'VoicePill.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "removed Startup shortcut" }

Get-Process pythonw -ErrorAction SilentlyContinue | Stop-Process -Force
Start-ScheduledTask -TaskName 'VoicePill'
Start-Sleep -Seconds 6

$task = Get-ScheduledTask -TaskName 'VoicePill'
Write-Host "`ntask: $($task.TaskName)  state: $($task.State)  runlevel: $($task.Principal.RunLevel)"
Write-Host "running: $([bool](Get-Process pythonw -ErrorAction SilentlyContinue))"
Write-Host "`nDone. VoicePill now starts elevated at every logon." -ForegroundColor Green
