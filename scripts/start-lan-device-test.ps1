$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend-react"
$viteEntry = Join-Path $frontendRoot "node_modules\vite\bin\vite.js"
$stateRoot = Join-Path $env:TEMP "jalora-lan-device-test"
$startedAt = Get-Date -Format "yyyyMMdd-HHmmss"
$runRoot = Join-Path $stateRoot $startedAt
$stateFile = Join-Path $stateRoot "active.json"
$backendPort = 8014
$frontendPort = 5184

if (-not (Test-Path $viteEntry)) {
    throw "Vite is not installed at $viteEntry. Run npm ci in frontend-react, then retry."
}

$occupiedPorts = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($backendPort, $frontendPort) }
if ($occupiedPorts) {
    $description = ($occupiedPorts | ForEach-Object {
        "$($_.LocalAddress):$($_.LocalPort) (PID $($_.OwningProcess))"
    }) -join ", "
    throw "LAN test ports are already in use: $description"
}

$addresses = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
    Where-Object {
        $_.AddressState -eq "Preferred" -and
        $_.IPAddress -notlike "127.*" -and
        $_.IPAddress -notlike "169.254.*" -and
        ($_.IPAddress -match "^10\." -or
         $_.IPAddress -match "^192\.168\." -or
         $_.IPAddress -match "^172\.(1[6-9]|2[0-9]|3[01])\.")
    }
$lanAddress = $addresses |
    Where-Object { $_.InterfaceAlias -match "Wi-?Fi|Wireless|Ethernet" } |
    Select-Object -First 1
if (-not $lanAddress) {
    $lanAddress = $addresses | Select-Object -First 1
}
if (-not $lanAddress) {
    throw "No private IPv4 address was found. Connect this PC to a private Wi-Fi or Ethernet network."
}

$pythonCommand = Get-Command python -ErrorAction Stop
$nodeCommand = Get-Command node -ErrorAction Stop
$lanIp = $lanAddress.IPAddress
$frontendUrl = "http://${lanIp}:${frontendPort}"
$environmentNames = @(
    "JALORA_ENV", "JALORA_JWT_SECRET", "JALORA_DATABASE_URL",
    "JALORA_DATA_DIR", "JALORA_UPLOAD_DIR", "JALORA_OUTPUTS_DIR",
    "JALORA_CREATIVE_DIR", "JALORA_WORKSPACES_DIR", "JALORA_CHROMA_DIR",
    "JALORA_PUBLIC_BASE_URL",
    "JALORA_CORS_ORIGINS", "JALORA_SCHEDULER_ENABLED",
    "JALORA_RATE_LIMIT_GLOBAL_PER_MIN", "JALORA_RATE_LIMIT_AUTH_PER_MIN",
    "JALORA_REDIS_URL", "JALORA_DEFAULT_PROVIDER",
    "JALORA_LOCAL_PROVIDER_PREFERENCE", "JALORA_EMBEDDING_PROVIDER",
    "JALORA_MEMORY_VECTORSTORE", "JALORA_SEARCH_PROVIDER",
    "JALORA_BROWSER_DRIVER", "JALORA_EMAIL_PROVIDER",
    "JALORA_CALENDAR_PROVIDER", "JALORA_DRIVE_PROVIDER",
    "JALORA_COMMS_PROVIDER", "JALORA_PAYMENTS_PROVIDER",
    "JALORA_TRAVEL_PROVIDER", "JALORA_MAPS_PROVIDER",
    "JALORA_WEATHER_PROVIDER", "JALORA_TRANSLATE_PROVIDER",
    "JALORA_OPENAI_API_KEY", "JALORA_ANTHROPIC_API_KEY",
    "JALORA_GOOGLE_API_KEY", "JALORA_GROQ_API_KEY",
    "JALORA_DEEPSEEK_API_KEY", "JALORA_MISTRAL_API_KEY",
    "JALORA_OPENROUTER_API_KEY", "JALORA_COHERE_API_KEY",
    "JALORA_CEREBRAS_API_KEY", "JALORA_PERPLEXITY_API_KEY",
    "JALORA_POOLSIDE_API_KEY", "JALORA_TAVILY_API_KEY",
    "JALORA_HF_TOKEN", "JALORA_DEEPGRAM_API_KEY",
    "JALORA_CREDENTIALS_ENCRYPTION_KEY",
    "JALORA_GMAIL_CLIENT_ID", "JALORA_GMAIL_CLIENT_SECRET",
    "JALORA_GMAIL_PUSH_TOKEN", "JALORA_TWILIO_ACCOUNT_SID",
    "JALORA_TWILIO_AUTH_TOKEN", "JALORA_TELNYX_API_KEY",
    "JALORA_TELNYX_PUBLIC_KEY", "JALORA_TELNYX_ACCOUNT_SID",
    "JALORA_SLACK_CLIENT_ID", "JALORA_SLACK_CLIENT_SECRET",
    "JALORA_GITHUB_CLIENT_ID", "JALORA_GITHUB_CLIENT_SECRET",
    "JALORA_NOTION_CLIENT_ID", "JALORA_NOTION_CLIENT_SECRET",
    "JALORA_SPOTIFY_CLIENT_ID", "JALORA_SPOTIFY_CLIENT_SECRET",
    "JALORA_DROPBOX_CLIENT_ID", "JALORA_DROPBOX_CLIENT_SECRET",
    "JALORA_STRIPE_CLIENT_ID", "JALORA_STRIPE_CLIENT_SECRET",
    "JALORA_AMADEUS_CLIENT_ID", "JALORA_AMADEUS_CLIENT_SECRET",
    "JALORA_GOOGLE_MAPS_API_KEY", "JALORA_LANGSMITH_API_KEY",
    "VITE_API_PROXY_TARGET"
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable(
        $name, "Process"
    )
}

New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
$dataRoot = Join-Path $runRoot "data"
$dataDirectories = @(
    $dataRoot,
    (Join-Path $dataRoot "uploads"),
    (Join-Path $dataRoot "outputs"),
    (Join-Path $dataRoot "workspaces"),
    (Join-Path $dataRoot "chroma")
)
New-Item -ItemType Directory -Force -Path $dataDirectories | Out-Null
$databasePath = Join-Path $dataRoot "jalora.db"
$databaseUrl = "sqlite:///" + ($databasePath -replace "\\", "/")

$env:JALORA_ENV = "test"
$env:JALORA_JWT_SECRET = "jalora-local-device-test-only-secret"
$env:JALORA_DATABASE_URL = $databaseUrl
$env:JALORA_DATA_DIR = $dataRoot
$env:JALORA_UPLOAD_DIR = Join-Path $dataRoot "uploads"
$env:JALORA_OUTPUTS_DIR = Join-Path $dataRoot "outputs"
$env:JALORA_CREATIVE_DIR = Join-Path $dataRoot "uploads"
$env:JALORA_WORKSPACES_DIR = Join-Path $dataRoot "workspaces"
$env:JALORA_CHROMA_DIR = Join-Path $dataRoot "chroma"
$env:JALORA_PUBLIC_BASE_URL = $frontendUrl
$env:JALORA_CORS_ORIGINS = "$frontendUrl,http://localhost:${frontendPort},http://127.0.0.1:${frontendPort}"
$env:JALORA_SCHEDULER_ENABLED = "false"
$env:JALORA_RATE_LIMIT_GLOBAL_PER_MIN = "0"
$env:JALORA_RATE_LIMIT_AUTH_PER_MIN = "1000"
$env:JALORA_REDIS_URL = ""
$env:JALORA_DEFAULT_PROVIDER = "lmstudio"
$env:JALORA_LOCAL_PROVIDER_PREFERENCE = "lmstudio"
$env:JALORA_EMBEDDING_PROVIDER = "local"
$env:JALORA_MEMORY_VECTORSTORE = "sql"
$env:JALORA_SEARCH_PROVIDER = "mock"
$env:JALORA_BROWSER_DRIVER = "fake"
$env:JALORA_EMAIL_PROVIDER = "mock"
$env:JALORA_CALENDAR_PROVIDER = "mock"
$env:JALORA_DRIVE_PROVIDER = "mock"
$env:JALORA_COMMS_PROVIDER = "mock"
$env:JALORA_PAYMENTS_PROVIDER = "mock"
$env:JALORA_TRAVEL_PROVIDER = "mock"
$env:JALORA_MAPS_PROVIDER = "mock"
$env:JALORA_WEATHER_PROVIDER = "mock"
$env:JALORA_TRANSLATE_PROVIDER = "mock"
$env:JALORA_OPENAI_API_KEY = ""
$env:JALORA_ANTHROPIC_API_KEY = ""
$env:JALORA_GOOGLE_API_KEY = ""
$env:JALORA_GROQ_API_KEY = ""
$env:JALORA_DEEPSEEK_API_KEY = ""
$env:JALORA_MISTRAL_API_KEY = ""
$env:JALORA_OPENROUTER_API_KEY = ""
$env:JALORA_COHERE_API_KEY = ""
$env:JALORA_CEREBRAS_API_KEY = ""
$env:JALORA_PERPLEXITY_API_KEY = ""
$env:JALORA_POOLSIDE_API_KEY = ""
$env:JALORA_TAVILY_API_KEY = ""
$env:JALORA_GMAIL_CLIENT_ID = ""
$env:JALORA_GMAIL_CLIENT_SECRET = ""
$env:JALORA_GMAIL_PUSH_TOKEN = ""
$env:JALORA_TWILIO_ACCOUNT_SID = ""
$env:JALORA_TWILIO_AUTH_TOKEN = ""
$env:JALORA_TELNYX_API_KEY = ""
$env:JALORA_TELNYX_PUBLIC_KEY = ""
$env:JALORA_TELNYX_ACCOUNT_SID = ""
$env:JALORA_DEEPGRAM_API_KEY = ""
$env:JALORA_HF_TOKEN = ""
$env:JALORA_CREDENTIALS_ENCRYPTION_KEY = ""
$env:JALORA_SLACK_CLIENT_ID = ""
$env:JALORA_SLACK_CLIENT_SECRET = ""
$env:JALORA_GITHUB_CLIENT_ID = ""
$env:JALORA_GITHUB_CLIENT_SECRET = ""
$env:JALORA_NOTION_CLIENT_ID = ""
$env:JALORA_NOTION_CLIENT_SECRET = ""
$env:JALORA_SPOTIFY_CLIENT_ID = ""
$env:JALORA_SPOTIFY_CLIENT_SECRET = ""
$env:JALORA_DROPBOX_CLIENT_ID = ""
$env:JALORA_DROPBOX_CLIENT_SECRET = ""
$env:JALORA_STRIPE_CLIENT_ID = ""
$env:JALORA_STRIPE_CLIENT_SECRET = ""
$env:JALORA_AMADEUS_CLIENT_ID = ""
$env:JALORA_AMADEUS_CLIENT_SECRET = ""
$env:JALORA_GOOGLE_MAPS_API_KEY = ""
$env:JALORA_LANGSMITH_API_KEY = ""
$env:VITE_API_PROXY_TARGET = "http://127.0.0.1:${backendPort}"

$backendOut = Join-Path $runRoot "backend.out.log"
$backendErr = Join-Path $runRoot "backend.err.log"
$frontendOut = Join-Path $runRoot "frontend.out.log"
$frontendErr = Join-Path $runRoot "frontend.err.log"
$backendProcess = $null
$frontendProcess = $null

try {
    $backendProcess = Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", "$backendPort"
        ) `
        -WorkingDirectory $backendRoot `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -PassThru

    $backendReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($backendProcess.HasExited) {
            throw "Test backend exited. Inspect $backendErr"
        }
        try {
            $health = Invoke-RestMethod `
                -Uri "http://127.0.0.1:${backendPort}/api/health" `
                -TimeoutSec 2
            if ($health.status -eq "ok" -and $health.env -eq "test") {
                $backendReady = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $backendReady) {
        throw "Test backend did not become ready. Inspect $backendErr"
    }

    $frontendProcess = Start-Process `
        -FilePath $nodeCommand.Source `
        -ArgumentList @(
            $viteEntry, "--host", "0.0.0.0",
            "--port", "$frontendPort", "--strictPort"
        ) `
        -WorkingDirectory $frontendRoot `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -PassThru

    $frontendReady = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($frontendProcess.HasExited) {
            throw "Test frontend exited. Inspect $frontendErr"
        }
        try {
            $response = Invoke-WebRequest `
                -Uri "http://127.0.0.1:${frontendPort}/api/health" `
                -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) {
                $frontendReady = $true
                break
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $frontendReady) {
        throw "Test frontend or its backend proxy did not become ready. Inspect $frontendErr"
    }

    $state = [ordered]@{
        backendPid = $backendProcess.Id
        frontendPid = $frontendProcess.Id
        backendPort = $backendPort
        frontendPort = $frontendPort
        lanIp = $lanIp
        frontendUrl = $frontendUrl
        backendLog = $backendErr
        frontendLog = $frontendErr
        dataDirectory = $dataRoot
        startedAt = (Get-Date).ToString("o")
    }
    $state | ConvertTo-Json | Set-Content -Path $stateFile -Encoding UTF8

    Write-Output "Jalora LAN test stack is ready."
    Write-Output "Open on this PC or a device on the same private Wi-Fi: $frontendUrl"
    Write-Output "Backend health: http://127.0.0.1:${backendPort}/api/health"
    Write-Output "Test data: $dataRoot"
    Write-Output "To stop safely: .\scripts\stop-lan-device-test.ps1"
    Write-Output "No production credentials or external messaging providers are enabled."
} catch {
    if ($frontendProcess -and -not $frontendProcess.HasExited) {
        Stop-Process -Id $frontendProcess.Id
    }
    if ($backendProcess -and -not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id
    }
    throw
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable(
            $name, $previousEnvironment[$name], "Process"
        )
    }
}
