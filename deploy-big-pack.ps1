# Deploy "big pack" changes since (and including) commit c510c00
# Usage: .\deploy-big-pack.ps1

param(
  [string]$User = "manager",
  [string]$HostName = "superinventory.local",
  [string]$RemotePath = "/opt/sweetshelves"
)

$baseCommit = "c510c00^"
$range = "$baseCommit..HEAD"
$changed = git diff --name-only $range | Where-Object { $_ -and ($_ -notmatch '\.db$') }
$extra = @(".env", "tokens.json", "listagent.db", "pricemaster.db", "listing_alerts.db", "fbstore.db")
$files = @($changed + $extra) | Select-Object -Unique | Where-Object { Test-Path $_ }

$root = @()
$templates = @()
$static = @()

foreach ($f in $files) {
  if ($f -like "templates/*") {
    $templates += $f
  } elseif ($f -like "static/*") {
    $static += $f
  } else {
    $root += $f
  }
}

Write-Host "🚀 Deploying big pack to $User@$HostName..." -ForegroundColor Cyan

if ($root.Count -gt 0) {
  Write-Host "Transferring root files..." -ForegroundColor Yellow
  scp @root "$User@$HostName`:$RemotePath/"
}
if ($templates.Count -gt 0) {
  Write-Host "Transferring templates..." -ForegroundColor Yellow
  scp @templates "$User@$HostName`:$RemotePath/templates/"
}
if ($static.Count -gt 0) {
  Write-Host "Transferring static assets..." -ForegroundColor Yellow
  scp @static "$User@$HostName`:$RemotePath/static/"
}

Write-Host "✅ Deployment complete!" -ForegroundColor Green

# Log deployment timestamp locally
$LogPath = Join-Path $PSScriptRoot "deploy-log.txt"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Stamp] Deployed to $User@$HostName ($RemotePath) via deploy-big-pack.ps1" | Out-File -FilePath $LogPath -Append -Encoding utf8

# Log deployment timestamp on the Pi
ssh "$User@$HostName" 'ts=$(date +%Y-%m-%dT%H:%M:%S); echo "[deploy-big-pack.ps1] $ts" >> /opt/sweetshelves/deploy-log.txt'
