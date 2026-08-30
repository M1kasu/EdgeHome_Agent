Get-NetTCPConnection -LocalPort 6053,6054,6055,6056 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
    $owner = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName
    Write-Output ("port {0} pid {1} {2}" -f $_.LocalPort, $_.OwningProcess, $owner)
}
docker ps --format '{{.Names}} {{.Ports}}' | Select-String '605'
