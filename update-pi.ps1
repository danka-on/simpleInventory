# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring Python files..." -ForegroundColor Yellow
scp .\app.py "$User@$HostName`:$RemotePath/"

Write-Host "Transferring template files..." -ForegroundColor Yellow
scp .\templates\unified_search.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\searchrack.html "$User@$HostName`:$RemotePath/templates/"

Write-Host "✅ Files transferred!" -ForegroundColor Green
Write-Host "🔄 Restarting Flask service..." -ForegroundColor Cyan

ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
