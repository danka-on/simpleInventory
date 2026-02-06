# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring files..." -ForegroundColor Yellow
scp app.py "$User@$HostName`:$RemotePath/app.py"
scp templates/position.html "$User@$HostName`:$RemotePath/templates/position.html"

# Log deployment timestamp locally
$LogPath = Join-Path $PSScriptRoot "deploy-log.txt"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Stamp] Deployed to $User@$HostName ($RemotePath) via update-pi.ps1" | Out-File -FilePath $LogPath -Append -Encoding utf8

# Log deployment timestamp on the Pi
ssh "$User@$HostName" 'ts=$(date +%Y-%m-%dT%H:%M:%S); echo "[update-pi.ps1] $ts" >> /opt/sweetshelves/deploy-log.txt'

Write-Host "✅ Files transferred!" -ForegroundColor Green

# Restart service
Write-Host "🔄 Restarting service..." -ForegroundColor Cyan
ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
