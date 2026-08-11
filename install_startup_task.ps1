param(
    [string]$TaskName = "Kotanamur Rental Monitor"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Monitor = Join-Path $ProjectRoot "monitor.py"
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Run setup.ps1 first; the Python environment is missing."
}
if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Create and configure .env before installing the task."
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument ('"{0}" --watch' -f $Monitor) `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Checks filtered Kotanamur rental listings and sends Telegram alerts." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed and started '$TaskName'. It will also start whenever you sign in."

