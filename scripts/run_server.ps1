param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8080,
    [string]$DataDirectory = "data/runtime"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    python -m src.server --host $HostAddress --port $Port --data-dir $DataDirectory
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
