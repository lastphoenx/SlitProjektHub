# scripts\01_create_backup.ps1
param()
$BASE = Split-Path -Parent $MyInvocation.MyCommand.Path
$ROOT = Split-Path -Parent $BASE
$venv = Join-Path $ROOT ".venv\Scripts\python.exe"
& $venv "$ROOT\src\\m04_backup.py"
