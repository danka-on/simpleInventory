#requires -Version 5.1
# Publish the Sweet Shelves Lister extension to the Pi's private update feed (static/lister/),
# the same way AmazingScout publishes: tests, immutable checksummed release, Dropbox mirror,
# upload payloads before metadata, verify on the server and over HTTPS.
#
# Several agent sessions share this worktree, so a release is never built from the working folder:
#   1. a lock on the Pi lets one publish run at a time;
#   2. HEAD must contain everything on GitHub and the commit the live feed was built from;
#   3. the version is the live feed's + 1 (nobody bumps manifest.json by hand);
#   4. tests, packaging and the Dropbox mirror run on a clean export of HEAD;
#   5. the one-line release commit is pushed before anything is uploaded;
#   6. the Pi refuses a latest.json that is not newer than the one it serves.
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
$sshOptions = @('-i', $IdentityFile, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10')
$python = 'py'
$pythonArgs = @('-3.13')
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { $python = 'python'; $pythonArgs = @() }
$env:PYTHONUTF8 = '1'
$locked = $false
$exportDir = Join-Path ([IO.Path]::GetTempPath()) "sweetshelves-lister-release-$PID"
$feedFile = Join-Path ([IO.Path]::GetTempPath()) "sweetshelves-lister-feed-$PID.json"

function Invoke-Release([string[]]$ReleaseArgs) {
    $lines = & $python @pythonArgs (Join-Path $repoRoot 'tools\release_lister.py') @ReleaseArgs
    $answer = ($lines | Select-Object -Last 1) | ConvertFrom-Json
    if (-not $answer.ok) { throw $answer.error }
    return $answer
}

# Shell snippets go to the Pi on stdin, not as ssh arguments: Windows argument quoting mangles the
# nested quotes. The trailing comment line absorbs the CR that Windows appends to piped input.
function Invoke-Remote([string]$Script) {
    return ($Script + "`n#") | & ssh @sshOptions $SshTarget 'bash -s'
}

Push-Location $repoRoot
try {
    # 1. One publish at a time. A lock older than 20 minutes is a crashed run and is taken over.
    $owner = "$env:COMPUTERNAME pid $PID $(Get-Date -Format s)"
    $lockScript = 'cd __ROOT__ && if mkdir .publish-lock 2>/dev/null; then echo "__OWNER__" > .publish-lock/owner; echo LOCKED; ' +
        'elif [ $(( $(date +%s) - $(stat -c %Y .publish-lock) )) -gt 1200 ]; then rm -rf .publish-lock && mkdir .publish-lock && echo "__OWNER__" > .publish-lock/owner && echo LOCKED; ' +
        'else echo BUSY; cat .publish-lock/owner 2>/dev/null; fi'
    $lockAnswer = Invoke-Remote ($lockScript.Replace('__ROOT__', $RemoteRoot).Replace('__OWNER__', $owner))
    if ($LASTEXITCODE -ne 0) { throw 'Cannot reach the Pi to take the publish lock.' }
    if (($lockAnswer | Select-Object -First 1) -ne 'LOCKED') { throw "Another Lister publish is running ($($lockAnswer | Select-Object -Last 1)). Nothing published." }
    $locked = $true

    # 2-3. What the feed serves now, and whether HEAD may replace it.
    $feedText = & ssh @sshOptions $SshTarget "cat $RemoteRoot/latest.json 2>/dev/null || true"
    [IO.File]::WriteAllText($feedFile, (($feedText | Out-String).Trim()))
    $check = Invoke-Release @('check', '--feed', $feedFile)
    $version = $check.version
    $base = $check.head
    Write-Host "Live feed $($check.feedVersion); releasing HEAD $($base.Substring(0, 9)) as $version."
    if ($check.uncommitted.Count) {
        Write-Warning ("Not in this release (uncommitted in lister-extension): " + ($check.uncommitted -join ', '))
    }

    # 4. A clean copy of HEAD with the new version; everything below builds from it.
    if (Test-Path -LiteralPath $exportDir) { Remove-Item -LiteralPath $exportDir -Recurse -Force }
    Invoke-Release @('export', '--version', $version, '--out', $exportDir) | Out-Null
    if (-not $SkipTests) {
        Push-Location $exportDir
        try {
            & $python @pythonArgs tools/run_checks.py test_lister
            if ($LASTEXITCODE -ne 0) { throw 'Lister server tests failed. Nothing published.' }
            & node test_lister_extension.cjs
            if ($LASTEXITCODE -ne 0) { throw 'Lister extension checks failed. Nothing published.' }
            & $python @pythonArgs tools/test_lister_release.py
            if ($LASTEXITCODE -ne 0) { throw 'Lister updater tests failed. Nothing published.' }
            & $python @pythonArgs tools/test_release_lister.py
            if ($LASTEXITCODE -ne 0) { throw 'Release tool tests failed. Nothing published.' }
        } finally { Pop-Location }
    }

    # 5. The release commit is on GitHub before any file reaches the feed.
    $commit = (Invoke-Release @('commit', '--version', $version, '--base', $base)).commit
    Write-Host "Release commit $($commit.Substring(0, 9)) pushed."

    & $python @pythonArgs tools/package_lister.py --source $exportDir --commit $commit
    if ($LASTEXITCODE -ne 0) { throw 'Extension packaging failed.' }
    if (-not $SkipMirror) {
        & "$PSScriptRoot\mirror-lister.ps1" -Destination $MirrorPath -Source (Join-Path $exportDir 'lister-extension')
        if ($LASTEXITCODE -ne 0) { throw 'Dropbox mirror failed.' }
    }
    $outputDir = Join-Path $repoRoot 'build\lister-updates'
    $metadata = Get-Content -LiteralPath (Join-Path $outputDir 'latest.json') -Raw | ConvertFrom-Json
    $release = $metadata.release
    if ($release -cnotmatch '^[a-f0-9]{20}$') { throw 'Invalid release identifier.' }
    if ($metadata.version -ne $version) { throw "Packaged version $($metadata.version) is not $version." }
    $page = Get-Content -LiteralPath (Join-Path $outputDir 'index.html') -Raw
    $moduleName = [regex]::Match($page, 'updater-[a-f0-9]{20}\.mjs').Value
    if (-not $moduleName) { throw 'Missing browser updater module.' }

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
    # 6. The Pi itself refuses to serve an older or equal version than the one it has.
    $newerCheck = 'cur=$(python3 -c "import json;print(json.load(open(''latest.json'')).get(''version'',''0''))" 2>/dev/null || echo 0); ' +
        'if [ "$cur" = "__V__" ] || [ "$(printf ''%s\n%s\n'' "$cur" "__V__" | sort -V | tail -n1)" != "__V__" ]; then ' +
        'echo "The feed already serves $cur; refusing __V__." >&2; rm -f *.pending-__R__; exit 3; fi; '
    $remotePublish = "cd $RemoteRoot && " + $newerCheck.Replace('__V__', $version).Replace('__R__', $release) +
        "echo '$($metadata.sha256)  releases/$release.zip' | sha256sum -c - && " +
        "echo '$($metadata.payloadSha256)  releases/$release.json' | sha256sum -c - && " +
        "mv update.ps1.pending-$release update.ps1 && mv index.html.pending-$release index.html && mv latest.json.pending-$release latest.json && " +
        "sha256sum update.ps1 index.html latest.json $moduleName"
    $remoteHashes = Invoke-Remote $remotePublish
    if ($LASTEXITCODE -ne 0) { throw 'The Pi could not verify or publish the release.' }
    foreach ($name in @('update.ps1', 'index.html', 'latest.json', $moduleName)) {
        $local = (Get-FileHash -LiteralPath (Join-Path $outputDir $name) -Algorithm SHA256).Hash.ToLower()
        if (-not ($remoteHashes | Where-Object { $_ -match "^$local\s+$([regex]::Escape($name))$" })) {
            throw "Server file verification failed for $name."
        }
    }
    Write-Host "Published Lister $version, release $release (commit $($commit.Substring(0, 9))) to $SshTarget`:$RemoteRoot (server checksums verified)." -ForegroundColor Green

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
} finally {
    if ($locked) { & ssh @sshOptions $SshTarget "rm -rf $RemoteRoot/.publish-lock" | Out-Null }
    if (Test-Path -LiteralPath $exportDir) { Remove-Item -LiteralPath $exportDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $feedFile) { Remove-Item -LiteralPath $feedFile -Force -ErrorAction SilentlyContinue }
    Pop-Location
}
