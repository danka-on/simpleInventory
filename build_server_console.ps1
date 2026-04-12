$ErrorActionPreference = 'Stop'

$repoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeName     = 'SweetShelvesServerConsole'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$buildRoot   = Join-Path $repoRoot '__desktop_build__'
$iconPath    = Join-Path $repoRoot 'static\favicon.ico'
$exePath     = Join-Path $repoRoot "$exeName.exe"

if (!(Test-Path $buildRoot)) {
    New-Item -ItemType Directory -Path $buildRoot | Out-Null
}

Push-Location $repoRoot
try {
    python -m pip install --upgrade pip pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }

    python -m PyInstaller `
        --noconfirm `
        --onefile `
        --windowed `
        --name $exeName `
        --icon $iconPath `
        --distpath $repoRoot `
        --workpath (Join-Path $buildRoot 'work') `
        --specpath $buildRoot `
        remote_server_console.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    # Desktop shortcut pointing to exe in the repo folder
    $shortcutPath = Join-Path $desktopPath 'Sweet Shelves Console.lnk'
    $shell    = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath       = $exePath
    $shortcut.IconLocation     = "$exePath,0"
    $shortcut.Description      = 'Sweet Shelves Server Console'
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Save()

    Write-Host "Built: $exePath"
    Write-Host "Shortcut: $shortcutPath"
}
finally {
    Pop-Location
}
