param(
    [switch]$NoTunnel
)

# Always run app.py with the virtual environment Python
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (!(Test-Path $venvPython)) {
    Write-Host "❌ Virtual environment Python not found at: $venvPython" -ForegroundColor Red
    Write-Host "Create it and install dependencies, then try again:" -ForegroundColor Yellow
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor Yellow
    Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "🚀 Starting Sweet Shelves with venv Python:" -ForegroundColor Cyan
Write-Host "    $venvPython" -ForegroundColor Gray

# Optionally pass a flag to control tunnel logic (app.py can read env var)
if ($NoTunnel) {
    $env:SWEETSHELVES_NO_TUNNEL = "1"
}

# Launch the app
& $venvPython "$root\app.py"
