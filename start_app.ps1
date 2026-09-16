# Start-Skript für SlitProjektHub
# Startet Backend (FastAPI) und Frontend (Streamlit) gleichzeitig

Write-Host "=== SlitProjektHub wird gestartet ===" -ForegroundColor Cyan
Write-Host ""

# Python-Pfad aus der virtuellen Umgebung (relativ zum Script)
$scriptPath = $PSScriptRoot
$venvPath = Join-Path $scriptPath ".venv\Scripts"
$pythonExe = Join-Path $venvPath "python.exe"
$streamlitExe = Join-Path $venvPath "streamlit.exe"

# Überprüfe, ob die virtuelle Umgebung existiert
if (-not (Test-Path $pythonExe)) {
    Write-Host "FEHLER: Virtuelle Umgebung nicht gefunden!" -ForegroundColor Red
    Write-Host "Bitte führen Sie zuerst 'python -m venv .venv' aus." -ForegroundColor Yellow
    exit 1
}

# Laufende Instanzen auf Port 8000 und 8501 beenden
Write-Host "Beende laufende Prozesse auf Port 8000/8501..." -ForegroundColor Yellow
$port8000 = netstat -ano | Select-String ":8000\s" | ForEach-Object { ($_ -split "\s+")[-1] } | Select-Object -Unique
foreach ($procId in $port8000) {
    if ($procId -match "^\d+$" -and $procId -ne "0") {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
$port8501 = netstat -ano | Select-String ":8501\s" | ForEach-Object { ($_ -split "\s+")[-1] } | Select-Object -Unique
foreach ($procId in $port8501) {
    if ($procId -match "^\d+$" -and $procId -ne "0") {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
}
Start-Sleep -Seconds 1

# Backend starten (FastAPI)
Write-Host "Backend wird gestartet..." -ForegroundColor Green
Start-Process pwsh -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot\backend'; & '$pythonExe' main.py"

# Kurze Pause, damit Backend Zeit hat zu starten
Start-Sleep -Seconds 2

# Frontend starten (Streamlit)
Write-Host "Frontend wird gestartet..." -ForegroundColor Green
Start-Process pwsh -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; & '$streamlitExe' run app"

Write-Host ""
Write-Host "=== Anwendungen wurden gestartet ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Backend (FastAPI):" -ForegroundColor Yellow
Write-Host "  - API: http://localhost:8000" -ForegroundColor White
Write-Host "  - Docs: http://localhost:8000/docs" -ForegroundColor White
Write-Host ""
Write-Host "Frontend (Streamlit):" -ForegroundColor Yellow
Write-Host "  - App: http://localhost:8501" -ForegroundColor White
Write-Host ""
Write-Host "Zum Beenden: Schließen Sie die beiden PowerShell-Fenster" -ForegroundColor Gray
