# Settings are loaded by run_workbench.py from this project's .env.
# This wrapper does not start HA, models, or ESPHome devices.
param(
    [string]$Python = 'python',
    [string]$HostAddress = '127.0.0.1',
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 'Python 3.11+ required; pass -Python with a supported interpreter path.')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $Python (Join-Path $projectRoot 'scripts/run_workbench.py') --host $HostAddress --port $Port
exit $LASTEXITCODE
