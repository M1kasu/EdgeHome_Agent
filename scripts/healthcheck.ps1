[CmdletBinding()]
param(
    [string]$HomeAssistantUrl = $(if ($env:HA_URL) { $env:HA_URL } else { 'http://127.0.0.1:8123' }),
    [string]$ModelUrl = $(if ($env:MODEL_URL) { $env:MODEL_URL } else { 'http://127.0.0.1:8080' })
)

$ErrorActionPreference = 'Stop'
$ha = Invoke-WebRequest -UseBasicParsing -Uri "$($HomeAssistantUrl.TrimEnd('/'))/" -TimeoutSec 15
$model = Invoke-WebRequest -UseBasicParsing -Uri "$($ModelUrl.TrimEnd('/'))/health" -TimeoutSec 15
if ($ha.StatusCode -ne 200 -or $model.StatusCode -ne 200) { throw 'Services did not return HTTP 200.' }
Write-Host "Home Assistant: HTTP $($ha.StatusCode)"
Write-Host "llama.cpp: HTTP $($model.StatusCode)"
