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

# Pass the requested tunnel mode and restore the caller's environment on exit.
$previousNoTunnel = $env:SWEETSHELVES_NO_TUNNEL
if ($NoTunnel) {
    $env:SWEETSHELVES_NO_TUNNEL = "1"
}

# Launch the app
Push-Location $root
try {
    & $venvPython "$root\app.py"
} finally {
    Pop-Location
    $env:SWEETSHELVES_NO_TUNNEL = $previousNoTunnel
}
