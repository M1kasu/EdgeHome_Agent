[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker compose -f deployment/docker-compose.yml up -d --build
& "$PSScriptRoot/healthcheck.ps1"
