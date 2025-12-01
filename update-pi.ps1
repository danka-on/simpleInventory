# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring files..." -ForegroundColor Yellow
# Add scp commands here as needed for specific tasks
# scp .\app.py "$User@$HostName`:$RemotePath/"

Write-Host "✅ Files transferred!" -ForegroundColor Green
Write-Host "🔄 Restarting Flask service..." -ForegroundColor Cyan

ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
