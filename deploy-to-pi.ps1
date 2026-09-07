# Stop after any failed transfer so partial deployments are not reported as complete.
$ErrorActionPreference = 'Stop'
function Invoke-ScpChecked {
    & scp @args
    if ($LASTEXITCODE -ne 0) { throw "SCP failed with exit code $LASTEXITCODE" }
}

Push-Location $PSScriptRoot
try {
    # Deploy updates to Raspberry Pi
    # Usage: .\deploy-to-pi.ps1

    $User = "dk"
    $HostName = "10.0.0.151"
    $SshKey = "C:/Users/boxatron/.ssh/sweet_shelves_pi"
    $RemotePath = "/opt/sweetshelves"

    Write-Host "🚀 Deploying to $User@$HostName..." -ForegroundColor Cyan

    # Stage only Python source: never send caches, configuration, or databases.
    # Install the package before its new entrypoint becomes visible on the Pi.
    $StageRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $PackageStage = Join-Path $StageRoot ('sweetshelves-deploy-' + [guid]::NewGuid().ToString('N'))
    $PackageCode = Join-Path $PackageStage 'sweetshelves'
    try {
        New-Item -ItemType Directory -Path $PackageCode -Force | Out-Null
        Get-ChildItem -LiteralPath .\sweetshelves -File -Filter '*.py' |
            Copy-Item -Destination $PackageCode
        Invoke-ScpChecked -r -i $SshKey $PackageCode "${User}@${HostName}:$RemotePath/"
    } finally {
        $ResolvedStage = [IO.Path]::GetFullPath($PackageStage)
        if (-not $ResolvedStage.StartsWith($StageRoot, [StringComparison]::OrdinalIgnoreCase) -or
            (Split-Path $ResolvedStage -Leaf) -notlike 'sweetshelves-deploy-*') {
            throw "Refusing to remove unexpected staging path: $ResolvedStage"
        }
        if (Test-Path -LiteralPath $ResolvedStage) {
            Remove-Item -LiteralPath $ResolvedStage -Recurse -Force
        }
    }

    # Transfer Python files
    Write-Host "Transferring Python modules..." -ForegroundColor Yellow
    $PythonModules = @(
        'app.py', 'DBmanager.py', 'BOLextractor.py', 'amazon_manager.py',
        'ebay_manager.py', 'ebay_mapping.py', 'enrich_sold_db.py', 'fba_inbound.py',
        'finder_aliases.py', 'finder_records.py', 'finder_search.py', 'inventory.py',
        'listing_mapping_routes.py', 'lot_matcher.py', 'marketplace_manager.py',
        'printer_manager.py', 'rawbol_manager.py', 'seller_analytics.py', 'token_manager.py', 'token_expiry.py',
        'organize_shelf_storage.py', 'rotating_backup.py', 'gunicorn_config.py'
    )
    Invoke-ScpChecked -i $SshKey @PythonModules "${User}@${HostName}:$RemotePath/"

    # Never SCP database files (.db) from PC to Pi.
    # The Pi has its own live databases.

    # Transfer templates
    Write-Host "Transferring templates..." -ForegroundColor Yellow
    Invoke-ScpChecked -i $SshKey .\templates\*.html "${User}@${HostName}:$RemotePath/templates/"

    # Transfer static files
    Write-Host "Transferring static assets..." -ForegroundColor Yellow
    Invoke-ScpChecked -i $SshKey .\static\*.js .\static\*.css "${User}@${HostName}:$RemotePath/static/"
    Invoke-ScpChecked -r -i $SshKey .\static\shelves\office "${User}@${HostName}:$RemotePath/static/shelves/"
    Invoke-ScpChecked -r -i $SshKey .\static\shelves\garage "${User}@${HostName}:$RemotePath/static/shelves/"
    Invoke-ScpChecked -r -i $SshKey .\static\shelves\hallway "${User}@${HostName}:$RemotePath/static/shelves/"
    Invoke-ScpChecked -r -i $SshKey .\static\shelves\misc "${User}@${HostName}:$RemotePath/static/shelves/"

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

    if ($LASTEXITCODE -ne 0) { throw "Remote deployment log failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
