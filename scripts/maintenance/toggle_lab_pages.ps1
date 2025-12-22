param(
  [ValidateSet("enable","disable")]
  [string]$Mode = "disable",
  [switch]$DryRun,
  # Optionale Pfade überschreiben (Standard: Workspace\app\pages bzw. app\pages_labs)
  [string]$PagesDir,
  [string]$LabsDir
)

# Root = Ordner überhalb von \scripts
$Root = Split-Path -Parent $PSScriptRoot
if (-not $PagesDir) { $PagesDir = Join-Path $Root "app\pages" }
if (-not $LabsDir)  { $LabsDir  = Join-Path $Root "app\pages_labs" }

# Liste der Labor-/Experiment-Seiten (Dateinamen oder Patterns)
# Passe diese Liste bei Bedarf an. Patterns wie *.py sind erlaubt und werden pro Eintrag aufgelöst.
$LabFiles = @(
  '10_RolesPreview.py',
  '11_RolesPreview_Grid.py',
  '12_Roles_MUI.py',
  '13_Roles_Native.py',
  '04_NewRoles.py',
  '12_Roles_NativePro.py',
  '21_Tasks_NativePro.py',
  '98_LLM_Demo.py', # neuer eindeutiger Name der LLM-Lab-Page
  'm08_llm.py'      # rückwärtskompatibel: falls noch vorhanden
)

# Sicherstellen, dass das Labs-Verzeichnis existiert
New-Item -ItemType Directory -Path $LabsDir -Force | Out-Null

function Move-IfExists {
  param(
    [Parameter(Mandatory)] [string]$SourcePath,
    [Parameter(Mandatory)] [string]$TargetPath
  )
  if (Test-Path $SourcePath -PathType Leaf) {
    if ($DryRun) {
      Write-Host "[DRYRUN] move: $SourcePath -> $TargetPath"
    } else {
      try {
        Move-Item -Force -LiteralPath $SourcePath -Destination $TargetPath
        Write-Host "moved: $SourcePath -> $TargetPath"
      } catch {
        Write-Warning "failed: $SourcePath -> $TargetPath : $($_.Exception.Message)"
      }
    }
  }
}

switch ($Mode) {
  'disable' {
    Write-Host "== Disable Labs (aus Menü entfernen) =="
    foreach ($pattern in $LabFiles) {
      $items = Get-ChildItem -Path $PagesDir -Filter $pattern -File -ErrorAction SilentlyContinue
      foreach ($it in $items) {
        $dst = Join-Path $LabsDir $it.Name
        Move-IfExists -SourcePath $it.FullName -TargetPath $dst
      }
    }
    Write-Host "Fertig: Labs deaktiviert."
  }
  'enable' {
    Write-Host "== Enable Labs (ins Menü aufnehmen) =="
    foreach ($pattern in $LabFiles) {
      $items = Get-ChildItem -Path $LabsDir -Filter $pattern -File -ErrorAction SilentlyContinue
      foreach ($it in $items) {
        $dst = Join-Path $PagesDir $it.Name
        Move-IfExists -SourcePath $it.FullName -TargetPath $dst
      }
    }
    Write-Host "Fertig: Labs aktiviert."
  }
}

Write-Host "PagesDir: $PagesDir"
Write-Host "LabsDir:  $LabsDir"
if ($DryRun) { Write-Host "(Hinweis: DRYRUN – keine Dateien wurden verschoben)" }
