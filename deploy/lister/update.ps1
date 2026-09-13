#requires -Version 5.1
# Sweet Shelves Lister updater for Windows PCs. Downloads the checksummed release from debby's
# private feed into the folder Chrome loads as an unpacked extension, verifying every file.
[CmdletBinding()]
param(
    [string]$ExtensionDir,
    [string]$ServerUrl = 'https://debby.taila97a84.ts.net',
    [switch]$InstallAutoUpdate,
    [switch]$RemoveAutoUpdate,
    [switch]$Scheduled
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$appName = 'Sweet Shelves Lister'
$appFolder = 'SweetShelvesLister'
$feedPath = 'lister'
$updaterRoot = Join-Path $env:LOCALAPPDATA "$appFolder\updater"
$configPath = Join-Path $updaterRoot 'settings.json'
$taskName = 'Sweet Shelves Lister Extension Update'
$workPath = $null
$updateLock = $null

function Assert-NoReparsePoint([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            if ((Get-Item -LiteralPath $current -Force).LinkType -in @('Junction', 'SymbolicLink')) {
                throw "Use a local folder without junctions or symbolic links: $current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Assert-ExtensionFolder([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { throw "Not a folder: $Path" }
    $items = @(Get-ChildItem -LiteralPath $Path -Force)
    if ($items.Count -eq 0) { return }
    $manifestPath = Join-Path $Path 'manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "The destination is not a $appName extension folder: $Path"
    }
    $existingManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($existingManifest.name -ne $appName -or $existingManifest.manifest_version -ne 3) {
        throw "The destination belongs to a different application: $Path"
    }
    if (@(Get-ChildItem -LiteralPath $Path -Recurse -Force | Where-Object {
        $_.LinkType -in @('Junction', 'SymbolicLink')
    }).Count -gt 0) { throw 'The extension folder contains junctions or symbolic links.' }
}

function Test-BrowserRunning {
    return @(Get-Process -Name chrome,msedge -ErrorAction SilentlyContinue).Count -gt 0
}

function Test-InstalledFiles([string]$Path, $Files) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) { return $false }
    $expected = @($Files.PSObject.Properties)
    $actual = @(Get-ChildItem -LiteralPath $Path -File -Recurse -Force)
    if ($actual.Count -ne $expected.Count) { return $false }
    foreach ($file in $expected) {
        $localPath = Join-Path $Path $file.Name
        if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) { return $false }
        if ((Get-FileHash -LiteralPath $localPath -Algorithm SHA256).Hash -ne $file.Value) { return $false }
    }
    return $true
}

function Install-Updater {
    New-Item -ItemType Directory -Path $updaterRoot -Force | Out-Null
    $savedScript = Join-Path $updaterRoot 'update.ps1'
    if ([IO.Path]::GetFullPath($PSCommandPath) -ne [IO.Path]::GetFullPath($savedScript)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $savedScript -Force
    }
    @{ extensionDir = $script:ExtensionDir; serverUrl = $script:ServerUrl } |
        ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    $launcher = '@echo off' + "`r`n" +
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0updater\update.ps1"' + "`r`n" +
        'pause' + "`r`n"
    Set-Content -LiteralPath (Join-Path (Split-Path $updaterRoot) "Update $appName.cmd") -Value $launcher -Encoding ASCII
    if ($InstallAutoUpdate) {
        $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
            -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$savedScript`" -Scheduled"
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Minutes 5)
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
            -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Description "Update $appName from debby while Chrome and Edge are closed." -Force | Out-Null
        Write-Host 'Automatic checks enabled every 5 minutes while you are signed in. Updates wait until Chrome and Edge are fully closed.'
    }
}

try {
    if ($RemoveAutoUpdate) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($task) { $task | Unregister-ScheduledTask -Confirm:$false }
        Write-Host "$appName automatic updates disabled. Manual updates still work."
        return
    }
    if (Test-Path -LiteralPath $configPath) {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        if (-not $ExtensionDir) { $ExtensionDir = $config.extensionDir }
        if (-not $PSBoundParameters.ContainsKey('ServerUrl')) { $ServerUrl = $config.serverUrl }
    }
    if (-not $ExtensionDir) { $ExtensionDir = Join-Path $env:LOCALAPPDATA "$appFolder\extension" }
    $ExtensionDir = [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($ExtensionDir)).TrimEnd('\', '/')
    if ($ExtensionDir -eq [IO.Path]::GetPathRoot($ExtensionDir).TrimEnd('\', '/') -or $ExtensionDir.StartsWith('\\')) {
        throw 'Choose a dedicated local extension folder, not a drive root or network share.'
    }
    Assert-NoReparsePoint $ExtensionDir
    Assert-ExtensionFolder $ExtensionDir
    $parentPath = Split-Path -Parent $ExtensionDir
    $server = [Uri]$ServerUrl
    if (-not $server.IsAbsoluteUri -or $server.Scheme -ne 'https' -or $server.UserInfo -or $server.Query -or $server.Fragment -or $server.AbsolutePath -ne '/') {
        throw 'ServerUrl must be a plain HTTPS origin, such as https://debby.taila97a84.ts.net.'
    }
    $ServerUrl = $server.GetLeftPart([UriPartial]::Authority)
    New-Item -ItemType Directory -Path $parentPath -Force | Out-Null
    $lockPath = Join-Path $parentPath ('.' + (Split-Path -Leaf $ExtensionDir) + '.sweetshelves-lister-update.lock')
    try { $updateLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [IO.IOException] { Write-Host "Another $appName update is already running."; return }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $baseUrl = "$ServerUrl/$feedPath"
    $metadata = Invoke-RestMethod -Uri "$baseUrl/latest.json?check=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" -TimeoutSec 45
    if ($metadata.schemaVersion -ne 1 -or $metadata.release -cnotmatch '^[a-f0-9]{20}$' -or
        $metadata.archive -cne "releases/$($metadata.release).zip" -or $metadata.sha256 -cnotmatch '^[a-f0-9]{64}$' -or -not $metadata.files) {
        throw 'Debby returned an invalid extension release description.'
    }
    $fileNames = @{}
    foreach ($file in $metadata.files.PSObject.Properties) {
        if ($file.Name -cnotmatch '^[a-zA-Z0-9_-]+(?:[./-][a-zA-Z0-9_-]+)*$' -or
            $file.Name -match '(^|/)(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(\.|/|$)' -or
            $file.Value -cnotmatch '^[a-f0-9]{64}$' -or $fileNames.ContainsKey($file.Name)) {
            throw 'The release contains an unsafe file name or invalid checksum.'
        }
        $fileNames[$file.Name] = $true
    }
    foreach ($required in @('manifest.json', 'background.js', 'sidepanel.html')) {
        if (-not $fileNames.ContainsKey($required)) { throw "Release is missing $required." }
    }
    if (Test-InstalledFiles $ExtensionDir $metadata.files) {
        Write-Host "$appName is current (release $($metadata.release))."
        if (-not $Scheduled) { Install-Updater }
        return
    }
    if ($Scheduled -and (Test-BrowserRunning)) {
        Write-Host 'Update available. Waiting for Chrome and Edge to fully close.'
        return
    }
    $workPath = Join-Path $parentPath ('.sweetshelves-lister-update-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workPath | Out-Null
    $zipPath = Join-Path $workPath 'release.zip'
    $payloadPath = Join-Path $workPath 'payload'
    $backupPath = Join-Path $workPath 'previous'
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$($metadata.archive)" -OutFile $zipPath -TimeoutSec 90
    if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $metadata.sha256) {
        throw 'Download checksum failed. The installed extension has not been changed.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $seen = @{}
        $totalBytes = 0L
        foreach ($entry in $archive.Entries) {
            if (-not $fileNames.ContainsKey($entry.FullName) -or $seen.ContainsKey($entry.FullName)) {
                throw 'The download contains unexpected or duplicate files.'
            }
            $seen[$entry.FullName] = $true
            $totalBytes += $entry.Length
            if ($totalBytes -gt 100MB) { throw 'Extension download exceeds the size limit.' }
        }
        if ($seen.Count -ne $fileNames.Count) { throw 'The download is missing extension files.' }
    } finally { $archive.Dispose() }
    [IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $payloadPath)
    if (-not (Test-InstalledFiles $payloadPath $metadata.files)) { throw 'Extracted file checksums failed.' }
    Assert-ExtensionFolder $payloadPath
    if ($Scheduled -and (Test-BrowserRunning)) { Write-Host 'Chrome or Edge opened. Update postponed.'; return }
    Assert-NoReparsePoint $ExtensionDir
    Assert-ExtensionFolder $ExtensionDir
    $hadExisting = Test-Path -LiteralPath $ExtensionDir
    if ($hadExisting) { Move-Item -LiteralPath $ExtensionDir -Destination $backupPath }
    try {
        Move-Item -LiteralPath $payloadPath -Destination $ExtensionDir
    } catch {
        if ($hadExisting) { Move-Item -LiteralPath $backupPath -Destination $ExtensionDir }
        throw
    }
    Write-Host "Updated $appName to release $($metadata.release). Folder: $ExtensionDir"
    if (-not $Scheduled) {
        Install-Updater
        if (Test-BrowserRunning) { Write-Host "In chrome://extensions, click Reload on $appName, then refresh your eBay and Seller Central tabs." }
        else { Write-Host 'The new build will load when you open Chrome.' }
        Write-Host 'First installation only: enable Developer mode in chrome://extensions and use Load unpacked with the folder above.'
    }
} catch {
    if ($Scheduled) {
        New-Item -ItemType Directory -Path $updaterRoot -Force | Out-Null
        "$(Get-Date -Format o) $($_.Exception.Message)" | Set-Content -LiteralPath (Join-Path $updaterRoot 'last-error.log')
    }
    Write-Error "$appName update failed: $($_.Exception.Message)" -ErrorAction Continue
    exit 1
} finally {
    if ($workPath -and (Test-Path -LiteralPath $workPath)) {
        $resolvedWork = [IO.Path]::GetFullPath($workPath)
        if ([IO.Path]::GetDirectoryName($resolvedWork) -ne $parentPath -or
            [IO.Path]::GetFileName($resolvedWork) -notmatch '^\.sweetshelves-lister-update-[a-f0-9]{32}$') {
            throw 'Refusing to remove a temporary folder outside the extension parent.'
        }
        # If rollback failed, preserve the old extension for manual recovery.
        if ((Test-Path -LiteralPath (Join-Path $workPath 'previous')) -and -not (Test-Path -LiteralPath $ExtensionDir)) {
            Write-Warning "Previous extension retained for recovery at $workPath\previous"
        } else { Remove-Item -LiteralPath $resolvedWork -Recurse -Force }
    }
    if ($updateLock) { $updateLock.Dispose() }
}
