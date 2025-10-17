# Quick verification functions for Flask app development
# Add this to your PowerShell profile: $PROFILE
# Or just dot-source it when needed: . .\dev-helpers.ps1

function Verify-FlaskRoutes {
    <#
    .SYNOPSIS
        Verify Flask routes in app.py
    .EXAMPLE
        Verify-FlaskRoutes shelfcreator
    .EXAMPLE
        Verify-FlaskRoutes  # Show all routes
    #>
    param(
        [string]$Pattern = ""
    )
    
    if ($Pattern) {
        python verify_routes.py $Pattern
    } else {
        python verify_routes.py
    }
}

function Check-AppFile {
    <#
    .SYNOPSIS
        Quick sanity check on app.py
    #>
    Write-Host "📊 App.py Status:" -ForegroundColor Cyan
    Write-Host "  Lines: $((Get-Content app.py).Count)" -ForegroundColor White
    Write-Host "  Size: $([math]::Round((Get-Item app.py).Length/1KB, 2)) KB" -ForegroundColor White
    Write-Host "  Modified: $((Get-Item app.py).LastWriteTime)" -ForegroundColor White
    
    # Check for common issues
    $content = Get-Content app.py -Raw
    if ($content -match "try:\s*\n\s*except") {
        Write-Host "  ⚠️  Empty try blocks detected" -ForegroundColor Yellow
    }
    if ($content -match "from \w+ import.*\n.*from \w+ import") {
        Write-Host "  ✓ Imports look OK" -ForegroundColor Green
    }
}

function Quick-Check {
    <#
    .SYNOPSIS
        Combined quick check: file status + route verification
    .EXAMPLE
        Quick-Check api/create_shelf
    #>
    param([string]$Pattern = "")
    
    Check-AppFile
    Write-Host ""
    if ($Pattern) {
        Verify-FlaskRoutes $Pattern
    } else {
        Write-Host "💡 Tip: Use 'Quick-Check <pattern>' to verify specific routes" -ForegroundColor Gray
    }
}

function Compare-DiskVsMemory {
    <#
    .SYNOPSIS
        Check if file on disk matches what Python sees
    #>
    param([string]$Pattern)
    
    Write-Host "🔍 Comparing disk vs memory for: $Pattern" -ForegroundColor Cyan
    
    # Check file
    $inFile = Select-String -Path app.py -Pattern $Pattern -Quiet
    Write-Host "  On disk: " -NoNewline
    if ($inFile) {
        Write-Host "✓ Found" -ForegroundColor Green
    } else {
        Write-Host "✗ NOT FOUND" -ForegroundColor Red
    }
    
    # Check Python
    $inPython = python -c "from app import app; print(any('$Pattern' in str(r) for r in app.url_map.iter_rules()))" 2>$null
    Write-Host "  In Flask: " -NoNewline
    if ($inPython -eq "True") {
        Write-Host "✓ Registered" -ForegroundColor Green
    } elseif ($inPython -eq "False") {
        Write-Host "✗ NOT REGISTERED" -ForegroundColor Red
    } else {
        Write-Host "✗ Import error" -ForegroundColor Red
    }
    
    if ($inFile -and ($inPython -ne "True")) {
        Write-Host "`n⚠️  MISMATCH DETECTED!" -ForegroundColor Yellow
        Write-Host "   File contains pattern but Flask doesn't see it." -ForegroundColor Yellow
        Write-Host "   → Try restarting your Flask server" -ForegroundColor White
    } elseif ($inFile -and ($inPython -eq "True")) {
        Write-Host "`n✅ All good!" -ForegroundColor Green
    }
}

# Aliases for convenience
Set-Alias -Name vfr -Value Verify-FlaskRoutes
Set-Alias -Name qc -Value Quick-Check
Set-Alias -Name cmp -Value Compare-DiskVsMemory

Write-Host "✅ Flask Dev Helpers Loaded!" -ForegroundColor Green
Write-Host "   Commands: " -ForegroundColor Cyan
Write-Host "     Verify-FlaskRoutes <pattern>  (alias: vfr)" -ForegroundColor White
Write-Host "     Quick-Check <pattern>         (alias: qc)" -ForegroundColor White
Write-Host "     Compare-DiskVsMemory <pattern> (alias: cmp)" -ForegroundColor White
Write-Host "     Check-AppFile" -ForegroundColor White
