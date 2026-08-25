# SlitProjektHub — Push main zu GitHub (SSH, kein WSL)
# Nutzung: .\scripts\git-push-main.ps1
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot

$env:GIT_SSH_COMMAND = "ssh -i C:/Users/tsant/.ssh/id_ed25519_github -o IdentitiesOnly=yes"
git push git@github.com:lastphoenx/SlitProjektHub.git main
