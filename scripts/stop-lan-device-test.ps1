$ErrorActionPreference = "Stop"

$stateFile = Join-Path $env:TEMP "jalora-lan-device-test\active.json"
if (-not (Test-Path $stateFile)) {
    Write-Output "No LAN test stack state file was found."
    exit 0
}

$state = Get-Content -Raw -Path $stateFile | ConvertFrom-Json
foreach ($entry in @(
    @{ Id = [int]$state.frontendPid; Port = [int]$state.frontendPort },
    @{ Id = [int]$state.backendPid; Port = [int]$state.backendPort }
)) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($entry.Id)" `
        -ErrorAction SilentlyContinue
    if (-not $process) {
        continue
    }
    if ($process.CommandLine -notmatch "--port\s+$($entry.Port)(\s|$)") {
        throw "Refusing to stop PID $($entry.Id): its command line does not match LAN test port $($entry.Port)."
    }
    Stop-Process -Id $entry.Id
    Write-Output "Stopped LAN test process PID $($entry.Id) on port $($entry.Port)."
}

Remove-Item -LiteralPath $stateFile
