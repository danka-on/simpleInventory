# Deploy updates to Raspberry Pi
# Usage: .\deploy-to-pi.ps1

$User = "dk"
$HostName = "10.0.0.151"
$SshKey = "C:/Users/boxatron/.ssh/sweet_shelves_pi"
$RemotePath = "/opt/sweetshelves"

Write-Host "🚀 Deploying to $User@$HostName..." -ForegroundColor Cyan

# Transfer Python files
Write-Host "Transferring Python modules..." -ForegroundColor Yellow
scp -i $SshKey .\app.py "${User}@${HostName}:$RemotePath/"
scp -i $SshKey .\DBmanager.py "${User}@${HostName}:$RemotePath/"
scp -i $SshKey .\organize_shelf_storage.py "${User}@${HostName}:$RemotePath/"

# Never SCP database files (.db) from PC to Pi.
# The Pi has its own live databases.

# Transfer templates
Write-Host "Transferring templates..." -ForegroundColor Yellow
scp -i $SshKey .\templates\position.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\barcode.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\multibarcode.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\items_to_list.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\shelfmanager.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\tools.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\cleanup.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\misc.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\item_prep.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\listingagent.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\listingagent_mobile.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\price_master.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\ready_to_ship.html "${User}@${HostName}:$RemotePath/templates/"
scp -i $SshKey .\templates\searchrack.html "${User}@${HostName}:$RemotePath/templates/"

# Transfer static files
Write-Host "Transferring static assets..." -ForegroundColor Yellow
scp -i $SshKey .\static\i18n.js "${User}@${HostName}:$RemotePath/static/"
scp -i $SshKey .\static\ios-scanner-keyboard.js "${User}@${HostName}:$RemotePath/static/"
scp -i $SshKey .\static\location-preview.js "${User}@${HostName}:$RemotePath/static/"
scp -i $SshKey .\static\barcode-entry-state.js "${User}@${HostName}:$RemotePath/static/"
scp -i $SshKey .\static\shelf-creator.css "${User}@${HostName}:$RemotePath/static/"
scp -i $SshKey .\static\shelf-creator.js "${User}@${HostName}:$RemotePath/static/"
scp -r -i $SshKey .\static\shelves\office "${User}@${HostName}:$RemotePath/static/shelves/"
scp -r -i $SshKey .\static\shelves\garage "${User}@${HostName}:$RemotePath/static/shelves/"
scp -r -i $SshKey .\static\shelves\hallway "${User}@${HostName}:$RemotePath/static/shelves/"
scp -r -i $SshKey .\static\shelves\misc "${User}@${HostName}:$RemotePath/static/shelves/"

Write-Host "✅ Deployment complete!" -ForegroundColor Green
Write-Host "💡 Restart the Flask app on the Pi to see changes" -ForegroundColor Cyan
Write-Host "💡 Pi cleanup dry run: ssh -i $SshKey $User@$HostName `"python3 $RemotePath/organize_shelf_storage.py $RemotePath/static/shelves --dry-run`"" -ForegroundColor Cyan
Write-Host "💡 Pi cleanup apply:   ssh -i $SshKey $User@$HostName `"python3 $RemotePath/organize_shelf_storage.py $RemotePath/static/shelves`"" -ForegroundColor Cyan

# Log deployment timestamp locally
$LogPath = Join-Path $PSScriptRoot "deploy-log.txt"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Stamp] Deployed to $User@$HostName ($RemotePath) via deploy-to-pi.ps1" | Out-File -FilePath $LogPath -Append -Encoding utf8

# Log deployment timestamp on the Pi
ssh -i $SshKey "${User}@${HostName}" 'ts=$(date +%Y-%m-%dT%H:%M:%S); echo "[deploy-to-pi.ps1] $ts" >> /opt/sweetshelves/deploy-log.txt'
