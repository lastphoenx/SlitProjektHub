#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Entfernt absolute Windows-Pfade aus Dokumentation für Portabilität

.DESCRIPTION
    Ersetzt spezifische Windows-Pfade mit generischen Platzhaltern:
    - C:\Users\username\Documents\Apps\SlitProjektHub → <PROJECT_ROOT>
    - Spezifische IP-Adressen → <YOUR_IP>
    - Spezifische Domains → <YOUR_DOMAIN>

.EXAMPLE
    .\remove_absolute_paths.ps1
#>

$ErrorActionPreference = "Stop"

Write-Host "╔═══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  🧹 Absolute Pfade bereinigen                         ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════╝`n" -ForegroundColor Cyan

# Zu bereinigendes Verzeichnis
$docsPath = Join-Path $PSScriptRoot "../../docs"

# Pfad-Ersetzungen (Reihenfolge ist wichtig!)
$replacements = @(
    # Windows absolute Pfade (alle Varianten)
    @{Pattern = 'C:\\Users\\username\\Documents\\Apps\\SlitProjektHub'; Replacement = '<PROJECT_ROOT>'},
    @{Pattern = 'C:/Users/username/Documents/Apps/SlitProjektHub'; Replacement = '<PROJECT_ROOT>'},
    @{Pattern = 'c:\\Users\\username\\Documents\\Apps\\SlitProjektHub'; Replacement = '<PROJECT_ROOT>'},
    @{Pattern = 'c:/Users/username/Documents/Apps/SlitProjektHub'; Replacement = '<PROJECT_ROOT>'},
    
    # /opt Pfade (Linux Server)
    @{Pattern = '/opt/projekthub'; Replacement = '<PROJECT_ROOT>'},
    
    # Username (nach Pfaden, damit nicht in Pfaden ersetzt wird)
    @{Pattern = 'lastphoenx'; Replacement = '<YOUR_GITHUB_USER>'},
    
    # Spezifische IPs (Beispiele)
    @{Pattern = '192\.168\.1\.50'; Replacement = '<YOUR_IP>'},
    @{Pattern = '10\.0\.0\.10'; Replacement = '<YOUR_IP>'},
    
    # Domain-Beispiele
    @{Pattern = 'projekthub\.mainedomain\.com'; Replacement = 'projekthub.<YOUR_DOMAIN>'},
    @{Pattern = 'auth\.mainedomain\.com'; Replacement = 'auth.<YOUR_DOMAIN>'},
    @{Pattern = 'mainedomain\.com'; Replacement = '<YOUR_DOMAIN>'}
)

# Markdown-Dateien finden
$files = Get-ChildItem -Path $docsPath -Recurse -Include "*.md" -File

Write-Host "📁 Gefundene Dateien: $($files.Count)`n" -ForegroundColor Yellow

$changedFiles = @()
$totalReplacements = 0

foreach ($file in $files) {
    $relativePath = $file.FullName.Replace((Get-Location).Path, ".")
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    $originalContent = $content
    $fileReplacements = 0
    
    # Alle Ersetzungen durchführen
    foreach ($item in $replacements) {
        $pattern = $item.Pattern
        $replacement = $item.Replacement
        $matches = [regex]::Matches($content, $pattern)
        if ($matches.Count -gt 0) {
            $content = $content -replace $pattern, $replacement
            $fileReplacements += $matches.Count
            $totalReplacements += $matches.Count
        }
    }
    
    # Nur speichern wenn Änderungen vorgenommen wurden
    if ($content -ne $originalContent) {
        Set-Content -Path $file.FullName -Value $content -Encoding UTF8 -NoNewline
        $changedFiles += $file
        Write-Host "  ✏️  $relativePath" -ForegroundColor Green
        Write-Host "      → $fileReplacements Ersetzungen" -ForegroundColor Gray
    }
}

Write-Host "`n" + ("=" * 60) -ForegroundColor Cyan
Write-Host "✅ Bereinigung abgeschlossen!" -ForegroundColor Green
Write-Host ("=" * 60) -ForegroundColor Cyan

Write-Host "`n📊 Statistik:" -ForegroundColor Yellow
Write-Host "   Geprüfte Dateien: $($files.Count)"
Write-Host "   Geänderte Dateien: $($changedFiles.Count)"
Write-Host "   Gesamt-Ersetzungen: $totalReplacements"

if ($changedFiles.Count -gt 0) {
    Write-Host "`n📝 Geänderte Dateien:" -ForegroundColor Cyan
    foreach ($file in $changedFiles) {
        $relativePath = $file.FullName.Replace((Get-Location).Path, ".")
        Write-Host "   - $relativePath" -ForegroundColor Gray
    }
    
    Write-Host "`n🔍 Nächste Schritte:" -ForegroundColor Yellow
    Write-Host "   1. Prüfe Änderungen: git diff docs/" -ForegroundColor Gray
    Write-Host "   2. Falls OK: git add docs/" -ForegroundColor Gray
    Write-Host "   3. Commit: git commit -m 'docs: Remove absolute paths for portability'" -ForegroundColor Gray
    Write-Host "   4. Push: git push origin main" -ForegroundColor Gray
} else {
    Write-Host "`n✨ Keine Änderungen nötig - Dokumentation ist bereits sauber!" -ForegroundColor Green
}

Write-Host ""
