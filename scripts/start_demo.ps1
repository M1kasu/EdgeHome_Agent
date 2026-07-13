[CmdletBinding()]
param(
    [int]$StartupTimeoutSeconds = 180
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

docker compose -f deployment/docker-compose.yml up -d --build
if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed; inspect the command output above before retrying."
}

# Docker Compose returns before Home Assistant and llama.cpp are healthy.
# Poll within a bounded period so a normal cold start is not reported as a failure.
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
do {
    try {
        & "$PSScriptRoot/healthcheck.ps1"
        exit 0
    }
    catch {
        if ((Get-Date) -ge $deadline) {
            throw "Services did not become ready within $StartupTimeoutSeconds seconds. Run 'docker compose -f deployment/docker-compose.yml ps' and inspect logs."
        }
        Start-Sleep -Seconds 5
    }
} while ($true)
