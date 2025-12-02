# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring files..." -ForegroundColor Yellow
scp .\app.py "$User@$HostName`:$RemotePath/"
scp .\templates\searchrack.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\index.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\misc.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\tools.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\items_to_list.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\item_prep.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\unified_search.html "$User@$HostName`:$RemotePath/templates/"

Write-Host "✅ Files transferred!" -ForegroundColor Green

# Restart service
Write-Host "🔄 Restarting service..." -ForegroundColor Cyan
ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
