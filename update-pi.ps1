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

Write-Host "✅ Files transferred!" -ForegroundColor Green

# Install new dependency (pytz) and restart service
Write-Host "📦 Installing dependencies and restarting service..." -ForegroundColor Cyan
ssh "$User@$HostName" "source $RemotePath/.venv/bin/activate && pip install pytz && sudo systemctl restart sweetshelves"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
