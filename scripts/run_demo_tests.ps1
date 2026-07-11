[CmdletBinding()]
param([switch]$SmokeOnly)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
Set-Location $root
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
if (Test-Path 'C:\tmp\edgehome_test_shims') { $env:PYTHONPATH = 'C:\tmp\edgehome_test_shims' }
$tests = @('tests/test_fast_path.py','tests/test_movie_scene.py','tests/test_leave_home_scene.py','tests/test_multi_turn_sleep.py','tests/test_failure_recovery.py','tests/test_knowledge_retriever.py','tests/test_energy_analyzer.py')
if (-not $SmokeOnly) { $tests += @('tests/test_protected_entities.py','tests/test_prompt_injection_safety.py','tests/test_invalid_json_recovery.py') }
& $python -m pytest -p pytest_asyncio.plugin -p edgehome_windows_hass -q @tests
