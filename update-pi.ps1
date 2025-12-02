# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring files..." -ForegroundColor Yellow
# Add files here as needed
# Example: scp app.py "$User@$HostName`:$RemotePath/app.py"

Write-Host "✅ Files transferred!" -ForegroundColor Green

# Restart service
Write-Host "🔄 Restarting service..." -ForegroundColor Cyan
ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
