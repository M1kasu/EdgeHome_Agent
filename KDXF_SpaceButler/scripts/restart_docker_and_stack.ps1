docker desktop start 2>&1 | Out-Null
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) {
    docker version --format '{{.Server.Version}}' 2>$null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 5
}
Start-Sleep -Seconds 10
Set-Location E:\code\znjj\KDXF_SpaceButler
docker compose -f deployment\docker-compose.yml --profile local-esphome up -d 2>&1 | Select-Object -Last 6
docker ps --filter name=spacebutler --format 'table {{.Names}}\t{{.Status}}' | Select-Object -First 18
