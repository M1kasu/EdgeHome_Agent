[CmdletBinding()]
param(
    [string]$HomeAssistantUrl = $(if ($env:HA_URL) { $env:HA_URL } else { 'http://127.0.0.1:8123' }),
    [string]$SimulatorUrl = $(if ($env:EDGEHOME_SIMULATOR_URL) { $env:EDGEHOME_SIMULATOR_URL } else { 'http://127.0.0.1:8090' }),
    [string]$AgentId = $(if ($env:EDGEHOME_AGENT_ID) { $env:EDGEHOME_AGENT_ID } else { 'conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome' })
)

$ErrorActionPreference = 'Stop'
if (-not $env:HA_TOKEN) {
    throw 'Set HA_TOKEN before running the MQTT light verification.'
}

$haBase = $HomeAssistantUrl.TrimEnd('/')
$simulatorBase = $SimulatorUrl.TrimEnd('/')
$headers = @{ Authorization = "Bearer $env:HA_TOKEN" }

function Invoke-Assist([string]$Text, [string]$ConversationId) {
    $request = @{ text = $Text; language = 'zh-Hans'; agent_id = $AgentId; conversation_id = $ConversationId } | ConvertTo-Json -Compress
    $payload = [System.Text.Encoding]::UTF8.GetBytes($request)
    return Invoke-RestMethod -Method Post -Uri "$haBase/api/conversation/process" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $payload -TimeoutSec 90
}

function Get-HomeAssistantState {
    return Invoke-RestMethod -Uri "$haBase/api/states/light.edgehome_living_room_main" -Headers $headers -TimeoutSec 15
}

$successCommand = (-join [char[]](0x628A, 0x5BA2, 0x5385, 0x4E3B, 0x706F, 0x4EAE, 0x5EA6, 0x8C03, 0x5230)) + '50%'
$failureCommand = -join [char[]](0x5173, 0x95ED, 0x5BA2, 0x5385, 0x4E3B, 0x706F)

Invoke-RestMethod -Method Post -Uri "$simulatorBase/admin/devices/living_room_main_light/reset" -ContentType 'application/json' -Body '{}' | Out-Null
Start-Sleep -Seconds 1
$brightness = Invoke-Assist $successCommand 'mqtt-light-success'
$haState = Get-HomeAssistantState
$device = Invoke-RestMethod -Uri "$simulatorBase/devices/living_room_main_light" -TimeoutSec 15
$events = Invoke-RestMethod -Uri "$simulatorBase/events?limit=50" -TimeoutSec 15

if ([int]$haState.attributes.brightness -notin 127, 128) {
    throw "Home Assistant brightness did not reach 50%: $($haState.attributes.brightness)"
}
if ([int]$device.state.brightness -notin 127, 128) {
    throw "Simulator brightness did not reach 50%: $($device.state.brightness)"
}
if (-not ($events.events.event_type -contains 'command_received') -or -not ($events.events.event_type -contains 'state_changed')) {
    throw 'Simulator event log is missing a command_received or state_changed event.'
}

Invoke-RestMethod -Method Post -Uri "$simulatorBase/admin/devices/living_room_main_light/fault" -ContentType 'application/json' -Body '{"mode":"ack_without_state_change"}' | Out-Null
$failure = Invoke-Assist $failureCommand 'mqtt-light-failure'
$afterFailure = Get-HomeAssistantState
$trace = Invoke-RestMethod -Uri "$haBase/api/states/sensor.edgehome_last_execution" -Headers $headers -TimeoutSec 15
Invoke-RestMethod -Method Post -Uri "$simulatorBase/admin/devices/living_room_main_light/reset" -ContentType 'application/json' -Body '{}' | Out-Null

if ($afterFailure.state -ne 'on') {
    throw "The simulated acknowledgement changed Home Assistant state unexpectedly: $($afterFailure.state)"
}
if ($trace.state -eq 'success') {
    throw 'EdgeHome incorrectly reported success after ack_without_state_change.'
}

Write-Host "MQTT success speech: $($brightness.response.speech.plain.speech)"
Write-Host "Failure speech: $($failure.response.speech.plain.speech)"
Write-Host "Verified MQTT light chain; final trace state: $($trace.state)"
