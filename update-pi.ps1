# Deploy updates to Raspberry Pi
# Usage: .\update-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying updates to $User@$HostName..." -ForegroundColor Cyan

# Transfer updated files
Write-Host "Transferring files..." -ForegroundColor Yellow
scp app.py "$User@$HostName`:$RemotePath/app.py"
scp templates/item_prep.html "$User@$HostName`:$RemotePath/templates/item_prep.html"
scp templates/item_prep_create_item.html "$User@$HostName`:$RemotePath/templates/item_prep_create_item.html"
scp templates/item_prep_diagnostic.html "$User@$HostName`:$RemotePath/templates/item_prep_diagnostic.html"
scp templates/marketplace_stats.html "$User@$HostName`:$RemotePath/templates/marketplace_stats.html"
scp templates/items_to_list.html "$User@$HostName`:$RemotePath/templates/items_to_list.html"

Write-Host "✅ Files transferred!" -ForegroundColor Green

# Restart service
Write-Host "🔄 Restarting service..." -ForegroundColor Cyan
ssh "$User@$HostName" "sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
