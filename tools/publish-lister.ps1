#requires -Version 5.1
# Publish the Sweet Shelves Lister extension to the Pi's private update feed (static/lister/),
# the same way AmazingScout publishes: tests, immutable checksummed release, Dropbox mirror,
# upload payloads before metadata, verify on the server and over HTTPS.
[CmdletBinding()]
param(
    [string]$SshTarget = 'dk@10.0.0.151',
    [string]$IdentityFile = 'C:/Users/boxatron/.ssh/sweet_shelves_pi',
    [string]$RemoteRoot = '/opt/sweetshelves/static/lister',
    [string]$TailnetUrl = 'https://debby.taila97a84.ts.net',
    [string]$MirrorPath = 'C:\Users\boxatron\Dropbox\Dakartee\lister-dist',
    [switch]$SkipTests,
    [switch]$SkipMirror
)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + [IO.Path]::PathSeparator + $env:PSModulePath
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $python = 'py'
    $pythonArgs = @('-3.13')
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $python = 'python'; $pythonArgs = @() }
    if (-not $SkipTests) {
        $env:PYTHONUTF8 = '1'
        & $python @pythonArgs tools/run_checks.py test_lister
        if ($LASTEXITCODE -ne 0) { throw 'Lister server tests failed. Nothing published.' }
        & node test_lister_extension.cjs
        if ($LASTEXITCODE -ne 0) { throw 'Lister extension checks failed. Nothing published.' }
        & $python @pythonArgs tools/test_lister_release.py
        if ($LASTEXITCODE -ne 0) { throw 'Lister updater tests failed. Nothing published.' }
    }
    & $python @pythonArgs tools/package_lister.py
    if ($LASTEXITCODE -ne 0) { throw 'Extension packaging failed.' }
    if (-not $SkipMirror) {
        & "$PSScriptRoot\mirror-lister.ps1" -Destination $MirrorPath
        if ($LASTEXITCODE -ne 0) { throw 'Dropbox mirror failed.' }
    }
    $outputDir = Join-Path $repoRoot 'build\lister-updates'
    $metadata = Get-Content -LiteralPath (Join-Path $outputDir 'latest.json') -Raw | ConvertFrom-Json
    $release = $metadata.release
    if ($release -cnotmatch '^[a-f0-9]{20}$') { throw 'Invalid release identifier.' }
    $page = Get-Content -LiteralPath (Join-Path $outputDir 'index.html') -Raw
    $moduleName = [regex]::Match($page, 'updater-[a-f0-9]{20}\.mjs').Value
    if (-not $moduleName) { throw 'Missing browser updater module.' }

    $sshOptions = @('-i', $IdentityFile, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
    & ssh @sshOptions $SshTarget "mkdir -p $RemoteRoot/releases"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the release directory on the Pi.' }
    # Immutable payloads first; clients only discover them once latest.json is renamed into place.
    & scp @sshOptions (Join-Path $outputDir "releases\$release.zip") "${SshTarget}:$RemoteRoot/releases/$release.zip"
    if ($LASTEXITCODE -ne 0) { throw 'Release upload failed.' }
    & scp @sshOptions (Join-Path $outputDir "releases\$release.json") "${SshTarget}:$RemoteRoot/releases/$release.json"
    if ($LASTEXITCODE -ne 0) { throw 'Browser payload upload failed.' }
    & scp @sshOptions (Join-Path $outputDir $moduleName) "${SshTarget}:$RemoteRoot/$moduleName"
    if ($LASTEXITCODE -ne 0) { throw 'Browser updater upload failed.' }
    foreach ($name in @('update.ps1', 'index.html', 'latest.json')) {
        & scp @sshOptions (Join-Path $outputDir $name) "${SshTarget}:$RemoteRoot/$name.pending-$release"
        if ($LASTEXITCODE -ne 0) { throw "Upload failed: $name" }
    }
    $remotePublish = "cd $RemoteRoot && echo '$($metadata.sha256)  releases/$release.zip' | sha256sum -c - && " +
        "echo '$($metadata.payloadSha256)  releases/$release.json' | sha256sum -c - && " +
        "mv update.ps1.pending-$release update.ps1 && mv index.html.pending-$release index.html && mv latest.json.pending-$release latest.json && " +
        "sha256sum update.ps1 index.html latest.json $moduleName"
    $remoteHashes = & ssh @sshOptions $SshTarget $remotePublish
    if ($LASTEXITCODE -ne 0) { throw 'The Pi could not verify or publish the release.' }
    foreach ($name in @('update.ps1', 'index.html', 'latest.json', $moduleName)) {
        $local = (Get-FileHash -LiteralPath (Join-Path $outputDir $name) -Algorithm SHA256).Hash.ToLower()
        if (-not ($remoteHashes | Where-Object { $_ -match "^$local\s+$([regex]::Escape($name))$" })) {
            throw "Server file verification failed for $name."
        }
    }
    Write-Host "Published release $release to $SshTarget`:$RemoteRoot (server checksums verified)." -ForegroundColor Green

    # Live HTTPS verification over the tailnet (the Cloudflare hostname needs a browser sign-in).
    try {
        $live = Invoke-RestMethod -Uri "$TailnetUrl/lister/latest.json?check=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" -TimeoutSec 20
        if ($live.release -ne $release -or $live.sha256 -ne $metadata.sha256) { throw 'Live release verification failed.' }
        $liveArchive = Join-Path $outputDir 'verified-live.zip'
        Invoke-WebRequest -UseBasicParsing -Uri "$TailnetUrl/lister/$($live.archive)" -OutFile $liveArchive -TimeoutSec 60
        if ((Get-FileHash -LiteralPath $liveArchive -Algorithm SHA256).Hash -ne $metadata.sha256) { throw 'Live download checksum failed.' }
        Write-Host "Verified over $TailnetUrl/lister/ (release $release)." -ForegroundColor Green
    } catch {
        Write-Warning "Tailnet verification skipped or failed: $($_.Exception.Message). The Cloudflare page https://pi.nexuscentralhq.org/lister/ serves the same files after sign-in."
    }
} finally { Pop-Location }
