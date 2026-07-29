$ErrorActionPreference = 'Stop'

$repoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeName     = 'SweetShelvesServerConsole'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$buildRoot   = Join-Path $repoRoot '__desktop_build__'
$distPath    = Join-Path $buildRoot 'dist'
$iconPath    = Join-Path $repoRoot 'static\favicon.ico'
$exePath     = Join-Path $repoRoot "$exeName.exe"
$versionedExePath = Join-Path $repoRoot "${exeName}_v2.exe"
$stagedExe   = Join-Path $distPath "$exeName.exe"

if (!(Test-Path $buildRoot)) {
    New-Item -ItemType Directory -Path $buildRoot | Out-Null
}

Push-Location $repoRoot
try {
    $pythonPath = (Get-Command python -ErrorAction Stop).Source
    & $pythonPath -m PyInstaller --version
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller is not installed for $pythonPath. Run: python -m pip install pyinstaller"
    }

    & $pythonPath remote_server_console.py --self-test
    if ($LASTEXITCODE -ne 0) { throw "Console self-test failed with exit code $LASTEXITCODE" }

    & $pythonPath -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name $exeName `
        --icon $iconPath `
        --distpath $distPath `
        --workpath (Join-Path $buildRoot 'work') `
        --specpath $buildRoot `
        remote_server_console.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
    if (!(Test-Path $stagedExe)) { throw "Expected build output was not created: $stagedExe" }

    # Replace the current executable only after a fully successful build.
    # If it is open, leave the old process untouched and install v2 beside it.
    $running = Get-Process -Name $exeName -ErrorAction SilentlyContinue
    $installedExePath = $exePath
    if ($running) {
        $installedExePath = $versionedExePath
        Copy-Item -LiteralPath $stagedExe -Destination $installedExePath -Force
        Write-Warning "The old console is running. Installed the update beside it: $installedExePath"
    }
    else {
        Copy-Item -LiteralPath $stagedExe -Destination $installedExePath -Force
    }

    # Desktop shortcut pointing to exe in the repo folder
    $shortcutPath = Join-Path $desktopPath 'Sweet Shelves Console.lnk'
    $shell    = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath       = $installedExePath
    $shortcut.IconLocation     = "$installedExePath,0"
    $shortcut.Description      = 'Sweet Shelves Server Console'
    $shortcut.WorkingDirectory = $repoRoot
    $shortcut.Save()

    Write-Host "Built: $installedExePath"
    Write-Host "Shortcut: $shortcutPath"
}
finally {
    Pop-Location
}
