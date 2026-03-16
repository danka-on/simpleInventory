# Deploy updates to Raspberry Pi
# Usage: .\deploy-to-pi.ps1

$User = "manager"
$HostName = "superinventory.local"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying to $User@$HostName..." -ForegroundColor Cyan

# Transfer Python files
Write-Host "Transferring Python modules..." -ForegroundColor Yellow
scp .\app.py "$User@$HostName`:$RemotePath/"
scp .\DBmanager.py "$User@$HostName`:$RemotePath/"
scp .\organize_shelf_storage.py "$User@$HostName`:$RemotePath/"

# Never SCP database files (.db) from PC to Pi.
# The Pi has its own live databases.

# Transfer templates
Write-Host "Transferring templates..." -ForegroundColor Yellow
scp .\templates\position.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\barcode.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\multibarcode.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\items_to_list.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\shelfmanager.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\tools.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\misc.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\item_prep.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\listingagent.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\listingagent_mobile.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\price_master.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\ready_to_ship.html "$User@$HostName`:$RemotePath/templates/"
scp .\templates\searchrack.html "$User@$HostName`:$RemotePath/templates/"

# Transfer static files
Write-Host "Transferring static assets..." -ForegroundColor Yellow
scp .\static\i18n.js "$User@$HostName`:$RemotePath/static/"
scp .\static\location-preview.js "$User@$HostName`:$RemotePath/static/"
scp .\static\shelf-creator.css "$User@$HostName`:$RemotePath/static/"
scp .\static\shelf-creator.js "$User@$HostName`:$RemotePath/static/"
scp -r .\static\shelves\office "$User@$HostName`:$RemotePath/static/shelves/"
scp -r .\static\shelves\garage "$User@$HostName`:$RemotePath/static/shelves/"
scp -r .\static\shelves\hallway "$User@$HostName`:$RemotePath/static/shelves/"
scp -r .\static\shelves\misc "$User@$HostName`:$RemotePath/static/shelves/"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
Write-Host "💡 Restart the Flask app on the Pi to see changes" -ForegroundColor Cyan
Write-Host "💡 Pi cleanup dry run: ssh $User@$HostName `"python3 $RemotePath/organize_shelf_storage.py $RemotePath/static/shelves --dry-run`"" -ForegroundColor Cyan
Write-Host "💡 Pi cleanup apply:   ssh $User@$HostName `"python3 $RemotePath/organize_shelf_storage.py $RemotePath/static/shelves`"" -ForegroundColor Cyan

# Log deployment timestamp locally
$LogPath = Join-Path $PSScriptRoot "deploy-log.txt"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Stamp] Deployed to $User@$HostName ($RemotePath) via deploy-to-pi.ps1" | Out-File -FilePath $LogPath -Append -Encoding utf8

# Log deployment timestamp on the Pi
ssh "$User@$HostName" 'ts=$(date +%Y-%m-%dT%H:%M:%S); echo "[deploy-to-pi.ps1] $ts" >> /opt/sweetshelves/deploy-log.txt'
