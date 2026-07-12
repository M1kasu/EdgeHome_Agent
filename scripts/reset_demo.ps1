[CmdletBinding()]
param(
    [string]$HomeAssistantUrl = $(if ($env:HA_URL) { $env:HA_URL } else { 'http://127.0.0.1:8123' }),
    [string]$SimulatorUrl = $(if ($env:EDGEHOME_SIMULATOR_URL) { $env:EDGEHOME_SIMULATOR_URL } else { 'http://127.0.0.1:8090' })
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
& $python "$PSScriptRoot/install_edgehome_demo.py" --config-dir "$root/deployment/homeassistant"
if (-not $env:HA_TOKEN) {
    throw 'Demo reset script is installed. Set HA_TOKEN for this session and retry to call script.edgehome_reset_demo_environment.'
}
$headers = @{ Authorization = "Bearer $env:HA_TOKEN" }
$body = @{ entity_id = 'script.edgehome_reset_demo_environment' } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "$($HomeAssistantUrl.TrimEnd('/'))/api/services/script/turn_on" -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
Invoke-RestMethod -Method Post -Uri "$($SimulatorUrl.TrimEnd('/'))/admin/devices/living_room_main_light/reset" -ContentType 'application/json' -Body '{}' | Out-Null
Write-Host 'Requested EdgeHome virtual-demo environment reset.'
