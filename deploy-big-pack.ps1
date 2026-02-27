# Deploy "big pack" changes since (and including) commit c510c00
# Usage: .\deploy-big-pack.ps1

param(
  [string]$User = "manager",
  [string]$HostName = "superinventory.local",
  [string]$RemotePath = "/opt/sweetshelves"
)

Set-Location $PSScriptRoot

$baseCommit = "c510c00^"
$range = "$baseCommit..HEAD"
$changedRange = git diff --name-only $range
$changedLocal = git ls-files -m -o --exclude-standard
$candidates = @($changedRange + $changedLocal) | Select-Object -Unique

$excludedExact = @(
  ".env",
  "tokens.json",
  "amazon_credentials.json"
)
$excludedRegex = @(
  '\.db$',
  '^__pycache__/',
  '^debug_uploads/',
  '^\.claude/',
  '\.pyc$'
)

$files = @()
foreach ($f in $candidates) {
  if (-not $f) { continue }
  if (-not (Test-Path $f)) { continue }
  if ($excludedExact -contains $f) { continue }

  $skip = $false
  foreach ($rx in $excludedRegex) {
    if ($f -match $rx) {
      $skip = $true
      break
    }
  }
  if ($skip) { continue }

  $files += $f
}
$files = $files | Select-Object -Unique

if ($files.Count -eq 0) {
  Write-Host "No deployable changes found." -ForegroundColor Yellow
  exit 0
}

$tmpDir = [System.IO.Path]::GetTempPath()
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$listPath = Join-Path $tmpDir "sweetshelves-deploy-$stamp.txt"
$tarPath = Join-Path $tmpDir "sweetshelves-deploy-$stamp.tar"
$remoteTar = "/tmp/sweetshelves-deploy-$stamp.tar"

$files | Out-File -FilePath $listPath -Encoding ascii

Write-Host "📦 Building archive with $($files.Count) files..." -ForegroundColor Yellow
tar -cf $tarPath -T $listPath
if ($LASTEXITCODE -ne 0) {
  throw "Failed to build deployment archive."
}

Write-Host "🚀 Uploading archive to $User@$HostName..." -ForegroundColor Cyan
scp $tarPath "$User@$HostName`:$remoteTar"
if ($LASTEXITCODE -ne 0) {
  throw "Failed to upload deployment archive."
}

Write-Host "📂 Extracting on Pi..." -ForegroundColor Yellow
ssh "$User@$HostName" "mkdir -p '$RemotePath' && tar -xf '$remoteTar' -C '$RemotePath' && rm -f '$remoteTar'"
if ($LASTEXITCODE -ne 0) {
  throw "Failed to extract deployment archive on Pi."
}

Remove-Item -Path $listPath, $tarPath -ErrorAction SilentlyContinue

Write-Host "✅ Deployment complete!" -ForegroundColor Green

# Log deployment timestamp locally
$LogPath = Join-Path $PSScriptRoot "deploy-log.txt"
$Stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$Stamp] Deployed to $User@$HostName ($RemotePath) via deploy-big-pack.ps1" | Out-File -FilePath $LogPath -Append -Encoding utf8

# Log deployment timestamp on the Pi
ssh "$User@$HostName" 'ts=$(date +%Y-%m-%dT%H:%M:%S); echo "[deploy-big-pack.ps1] $ts" >> /opt/sweetshelves/deploy-log.txt'
