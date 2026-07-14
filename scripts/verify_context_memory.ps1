[CmdletBinding()]
param(
    [string]$HomeAssistantUrl = $(if ($env:HA_URL) { $env:HA_URL } else { 'http://127.0.0.1:8123' }),
    [string]$SimulatorUrl = $(if ($env:EDGEHOME_SIMULATOR_URL) { $env:EDGEHOME_SIMULATOR_URL } else { 'http://127.0.0.1:8090' }),
    [string]$AgentId = $(if ($env:EDGEHOME_AGENT_ID) { $env:EDGEHOME_AGENT_ID } else { 'conversation.home_llama_3_2_3b_edgehome_home_llama_3_2_3b_edgehome' }),
    [switch]$RestartHomeAssistant
)

$ErrorActionPreference = 'Stop'
if (-not $env:HA_TOKEN) {
    throw 'Set HA_TOKEN before running the context-memory verification.'
}

$haBase = $HomeAssistantUrl.TrimEnd('/')
$simulatorBase = $SimulatorUrl.TrimEnd('/')
$headers = @{ Authorization = "Bearer $env:HA_TOKEN" }
$reportDirectory = Join-Path $PSScriptRoot '..\reports\mqtt_light_acceptance'
$reportPath = Join-Path $reportDirectory 'context_memory_results.jsonl'
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null

function Convert-FromCodePoints([int[]]$CodePoints) {
    return -join [char[]]$CodePoints
}

function Invoke-Assist([string]$Text, [string]$ConversationId) {
    $request = @{ text = $Text; language = 'zh-Hans'; agent_id = $AgentId }
    if ($ConversationId) {
        $request.conversation_id = $ConversationId
    }
    $payload = [System.Text.Encoding]::UTF8.GetBytes(($request | ConvertTo-Json -Compress))
    return Invoke-RestMethod -Method Post -Uri "$haBase/api/conversation/process" -Headers $headers -ContentType 'application/json; charset=utf-8' -Body $payload -TimeoutSec 90
}

function Get-LightState {
    return Invoke-RestMethod -Uri "$haBase/api/states/light.edgehome_living_room_main" -Headers $headers -TimeoutSec 15
}

function Wait-ForAssistant {
    $deadline = (Get-Date).AddSeconds(120)
    do {
        try {
            Invoke-RestMethod -Uri "$haBase/api/states/$AgentId" -Headers $headers -TimeoutSec 5 | Out-Null
            Get-LightState | Out-Null
            return
        }
        catch {
            Start-Sleep -Seconds 3
        }
    } while ((Get-Date) -lt $deadline)
    throw 'Home Assistant conversation agent did not become ready after restart.'
}

$setFifty = (Convert-FromCodePoints @(0x628A, 0x5BA2, 0x5385, 0x4E3B, 0x706F, 0x4EAE, 0x5EA6, 0x8C03, 0x5230)) + '50%'
$dimmer = Convert-FromCodePoints @(0x518D, 0x6697, 0x4E00, 0x70B9)
$turnOff = Convert-FromCodePoints @(0x5173, 0x6389, 0x5427)

Invoke-RestMethod -Method Post -Uri "$simulatorBase/admin/devices/living_room_main_light/reset" -ContentType 'application/json' -Body '{}' | Out-Null
Start-Sleep -Seconds 1

$first = Invoke-Assist $setFifty ''
$conversationId = [string]$first.conversation_id
if (-not $conversationId) {
    throw 'The first Assist response did not return a conversation_id.'
}
$afterFirst = Get-LightState
if ([int]$afterFirst.attributes.brightness -notin 127, 128) {
    throw "First turn did not reach 50%: $($afterFirst.attributes.brightness)"
}

if ($RestartHomeAssistant) {
    $composePath = Join-Path $PSScriptRoot '..\deployment\docker-compose.yml'
    docker compose -f $composePath restart home-assistant | Out-Host
    Wait-ForAssistant
}

$second = Invoke-Assist $dimmer $conversationId
$afterSecond = Get-LightState
if ([string]$second.conversation_id -ne $conversationId) {
    throw 'The second Assist request did not retain the first conversation_id.'
}
if ([int]$afterSecond.attributes.brightness -notin 101, 102) {
    throw "Context continuation did not dim to 40%: $($afterSecond.attributes.brightness)"
}

$third = Invoke-Assist $turnOff $conversationId
$afterThird = Get-LightState
if ([string]$third.conversation_id -ne $conversationId -or $afterThird.state -ne 'off') {
    throw 'Pronoun continuation did not close the same MQTT light.'
}

$isolated = Invoke-Assist $dimmer ''
$afterIsolated = Get-LightState
if ($afterIsolated.state -ne 'off') {
    throw 'A new conversation inherited a previous session target.'
}

$result = [ordered]@{
    timestamp = (Get-Date).ToUniversalTime().ToString('o')
    restart_home_assistant = [bool]$RestartHomeAssistant
    conversation_id = $conversationId
    first = @{ input = $setFifty; state = $afterFirst.state; brightness = [int]$afterFirst.attributes.brightness; speech = $first.response.speech.plain.speech }
    second = @{ input = $dimmer; conversation_id = $second.conversation_id; state = $afterSecond.state; brightness = [int]$afterSecond.attributes.brightness; speech = $second.response.speech.plain.speech }
    third = @{ input = $turnOff; conversation_id = $third.conversation_id; state = $afterThird.state; speech = $third.response.speech.plain.speech }
    isolated = @{ input = $dimmer; state = $afterIsolated.state; speech = $isolated.response.speech.plain.speech }
}
$result | ConvertTo-Json -Depth 6 -Compress | Add-Content -Path $reportPath -Encoding UTF8
Write-Host "Context-memory verification passed. Evidence: $reportPath"

Invoke-RestMethod -Method Post -Uri "$simulatorBase/admin/devices/living_room_main_light/reset" -ContentType 'application/json' -Body '{}' | Out-Null
