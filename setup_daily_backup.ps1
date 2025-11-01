# PowerShell script to set up Windows Task Scheduler for daily backups
# Run this in PowerShell as Administrator

$ErrorActionPreference = "Stop"

Write-Host "=" -NoNewline -ForegroundColor Cyan
Write-Host ("=" * 69) -ForegroundColor Cyan
Write-Host "SETUP DAILY AUTOMATED BACKUP FOR searchRack.db" -ForegroundColor Cyan
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host ""

# Get the current directory
$ScriptDir = Get-Location
$PythonScript = Join-Path $ScriptDir "rotating_backup.py"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"

# Check if files exist
if (-not (Test-Path $PythonScript)) {
    Write-Host "ERROR: rotating_backup.py not found!" -ForegroundColor Red
    Write-Host "Expected location: $PythonScript" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "ERROR: Python virtual environment not found!" -ForegroundColor Red
    Write-Host "Expected location: $VenvPython" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Trying system Python instead..." -ForegroundColor Yellow
    $VenvPython = "python"
}

Write-Host "Configuration:" -ForegroundColor Green
Write-Host "  Script: $PythonScript" -ForegroundColor White
Write-Host "  Python: $VenvPython" -ForegroundColor White
Write-Host "  Working Directory: $ScriptDir" -ForegroundColor White
Write-Host ""

# Task Scheduler settings
$TaskName = "SimpleInventory-DailyBackup"
$TaskDescription = "Daily rotating backup of searchRack.db (10-day rotation)"
$BackupTime = "02:00AM"  # 2 AM daily

Write-Host "Task Scheduler Settings:" -ForegroundColor Green
Write-Host "  Task Name: $TaskName" -ForegroundColor White
Write-Host "  Run Time: $BackupTime daily" -ForegroundColor White
Write-Host "  User: $env:USERNAME" -ForegroundColor White
Write-Host ""

$Confirm = Read-Host "Create this scheduled task? (yes/no)"
if ($Confirm -ne "yes") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# Remove existing task if it exists
$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask) {
    Write-Host "Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Create the scheduled task
$Action = New-ScheduledTaskAction `
    -Execute $VenvPython `
    -Argument "`"$PythonScript`" backup" `
    -WorkingDirectory $ScriptDir

$Trigger = New-ScheduledTaskTrigger -Daily -At $BackupTime

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable:$false

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType S4U `
    -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Description $TaskDescription `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal | Out-Null
    
    Write-Host ""
    Write-Host "SUCCESS! Daily backup task created." -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Details:" -ForegroundColor Cyan
    Write-Host "  - Runs every day at $BackupTime" -ForegroundColor White
    Write-Host "  - Keeps 10 days of rotating backups" -ForegroundColor White
    Write-Host "  - Backups stored in: $ScriptDir\backups\" -ForegroundColor White
    Write-Host ""
    Write-Host "To manage this task:" -ForegroundColor Cyan
    Write-Host "  - Open Task Scheduler (taskschd.msc)" -ForegroundColor White
    Write-Host "  - Look for '$TaskName'" -ForegroundColor White
    Write-Host ""
    Write-Host "To test the backup manually:" -ForegroundColor Cyan
    Write-Host "  python rotating_backup.py backup" -ForegroundColor White
    Write-Host ""
    Write-Host "To see existing backups:" -ForegroundColor Cyan
    Write-Host "  python rotating_backup.py list" -ForegroundColor White
    Write-Host ""
    
    # Offer to run first backup now
    $RunNow = Read-Host "Would you like to create the first backup now? (yes/no)"
    if ($RunNow -eq "yes") {
        Write-Host ""
        Write-Host "Creating first backup..." -ForegroundColor Yellow
        & $VenvPython $PythonScript backup
    }
    
} catch {
    Write-Host ""
    Write-Host "ERROR creating scheduled task:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "You may need to run PowerShell as Administrator." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host ("=" * 70) -ForegroundColor Cyan
Write-Host "SETUP COMPLETE!" -ForegroundColor Green
Write-Host ("=" * 70) -ForegroundColor Cyan
