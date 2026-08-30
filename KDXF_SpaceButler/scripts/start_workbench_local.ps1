# Local demo startup: local docker ESPHome nodes published on 6053-6056.
# LLM: EdgeHome llama.cpp (3B) already running on 8080.
$secretsPath = 'E:\code\znjj\KDXF_SpaceButler\deployment\esphome\secrets.yaml'
$psk = (Select-String -Path $secretsPath -Pattern 'api_key:\s*"?([^"]+)"?').Matches[0].Groups[1].Value
$env:ESPHOME_NOISE_PSK = $psk
$env:ESPHOME_HOST = 'host.docker.internal'
$env:SPACEBUTLER_LLM_BASE_URL = 'http://127.0.0.1:8080'
$env:SPACEBUTLER_LLM_TIMEOUT = '240'
Set-Location E:\code\znjj\KDXF_SpaceButler
python scripts\run_workbench.py --port 8765
