# SlitProjektHub — Push main zu GitHub (SSH)
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repoRoot
$key = $env:SLIT_GITHUB_SSH_KEY
if (-not $key) { $key = "$env:USERPROFILE\.ssh\id_ed25519_github" }
$env:GIT_SSH_COMMAND = "ssh -i $key -o IdentitiesOnly=yes"
git push git@github.com:lastphoenx/SlitProjektHub.git main
