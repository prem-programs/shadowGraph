# Make a clean zip of the project (no node_modules, builds, virtualenvs or secrets) next to the project folder.
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Name = Split-Path -Leaf $Root
$Out = Join-Path (Split-Path -Parent $Root) "$Name.zip"
$Staging = Join-Path $env:TEMP "shadowgraph-package"
if (Test-Path $Staging) { Remove-Item $Staging -Recurse -Force }
$exclude = @("node_modules", "dist", ".venv", "venv", "__pycache__", ".pytest_cache", ".git")
robocopy $Root (Join-Path $Staging $Name) /E /XD $exclude /XF ".env" "*.pyc" "*.zip" /NFL /NDL /NJH /NJS /NP | Out-Null
if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $Staging $Name) -DestinationPath $Out
Remove-Item $Staging -Recurse -Force
Write-Host "Wrote $Out"
