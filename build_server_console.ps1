$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$exeName = 'SweetShelvesServerConsole'
$desktopPath = [Environment]::GetFolderPath('Desktop')
$buildRoot = Join-Path $repoRoot '__desktop_build__'
$iconPath = Join-Path $repoRoot 'static\favicon.ico'

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
        --distpath $desktopPath `
        --workpath (Join-Path $buildRoot 'work') `
        --specpath $buildRoot `
        remote_server_console.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    $exampleConfig = Join-Path $repoRoot 'server_control_config.example.json'
    $desktopConfig = Join-Path $desktopPath 'server_control_config.example.json'
    Copy-Item $exampleConfig $desktopConfig -Force

    Write-Host "Built $exeName.exe on your desktop."
    Write-Host "Config example copied to $desktopConfig"
}
finally {
    Pop-Location
}
