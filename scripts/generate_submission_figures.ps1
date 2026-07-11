[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
Set-Location $root
& $python scripts/generate_submission_figures.py
Write-Host 'Submission figures and audit data were updated in competition_submission/assets.'
