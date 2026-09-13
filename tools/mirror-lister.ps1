#requires -Version 5.1
# Mirror the Sweet Shelves Lister extension folder to Dropbox and verify every file hash.
param([string]$Destination = 'C:\Users\boxatron\Dropbox\Dakartee\lister-dist')
$ErrorActionPreference = 'Stop'
$source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\lister-extension'))
$target = [IO.Path]::GetFullPath($Destination).TrimEnd('\')
if ((Split-Path -Leaf $target) -ne 'lister-dist' -or $target -eq $source -or
    $source.StartsWith($target + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $target.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Mirror destination must be a separate dedicated lister-dist folder.'
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'manifest.json'))) { throw 'The extension folder is missing manifest.json.' }
$ancestor = $target
while ($ancestor) {
    if ((Test-Path -LiteralPath $ancestor) -and
        ((Get-Item -LiteralPath $ancestor -Force).LinkType -in @('Junction', 'SymbolicLink'))) {
        throw "Mirror destination contains a junction or symbolic link: $ancestor"
    }
    $ancestor = [IO.Path]::GetDirectoryName($ancestor)
}
if (Test-Path -LiteralPath $target) {
    if (@(Get-ChildItem -LiteralPath $target -Recurse -Force | Where-Object {
        $_.LinkType -in @('Junction', 'SymbolicLink')
    }).Count) { throw 'Mirror destination contains a junction or symbolic link.' }
    if (@(Get-ChildItem -LiteralPath $target -Force).Count) {
        $manifest = Get-Content -LiteralPath (Join-Path $target 'manifest.json') -Raw | ConvertFrom-Json
        if ($manifest.name -ne 'Sweet Shelves Lister') { throw 'Mirror destination is not Sweet Shelves Lister.' }
    }
}
Write-Host "Mirroring $source to $target"
& robocopy $source $target /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NP /XD __pycache__
if ($LASTEXITCODE -ge 8) { throw 'Extension mirror failed.' }
$sourceFiles = @(Get-ChildItem -LiteralPath $source -Recurse -File | Where-Object { $_.FullName -notmatch '__pycache__' })
$targetFiles = @(Get-ChildItem -LiteralPath $target -Recurse -File)
if ($sourceFiles.Count -ne $targetFiles.Count) { throw 'Mirror file count differs from the extension folder.' }
foreach ($file in $sourceFiles) {
    $relative = $file.FullName.Substring($source.Length + 1)
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $target $relative)).Hash) {
        throw "Mirror checksum failed: $relative"
    }
}
$global:LASTEXITCODE = 0
Write-Host "Verified all $($sourceFiles.Count) mirrored files."
