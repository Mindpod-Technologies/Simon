# Simon — Windows installer (v1). Local-first, idempotent.
#
#   powershell -ExecutionPolicy Bypass -File deploy\install_windows.ps1
#   ... -SkipModels            defer the ~18 GB model downloads
#   ... -NonInteractive        accept all defaults
#
# Mirrors install_mac.sh: prerequisites (Python 3.11, Ollama via winget),
# model fleet, venv, .env wizard, default mcp.json, then registers Simon
# as a Scheduled Task (logon trigger + restart-on-failure) instead of
# launchd. The monitor portal is a second task on port 8789.

param(
    [switch]$SkipModels,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
$SimonDir = if ($env:SIMON_DIR) { $env:SIMON_DIR } else { "$HOME\simon" }

function Log($msg)  { Write-Host "==> $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host " !! $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "ERR $msg" -ForegroundColor Red; exit 1 }

function Ask($prompt, $default = "") {
    if ($NonInteractive) { return $default }
    $shown = if ($default) { " [$default]" } else { "" }
    $reply = Read-Host "    $prompt$shown"
    if ([string]::IsNullOrWhiteSpace($reply)) { return $default }
    return $reply
}

Log "Simon installer (Windows v1)"

# --- 1. Prerequisites -------------------------------------------------------
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Die "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Log "Installing Python 3.11 via winget..."
    winget install -e --id Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Log "Installing Ollama via winget..."
    winget install -e --id Ollama.Ollama --silent --accept-package-agreements --accept-source-agreements
}

# --- 2. Repo ----------------------------------------------------------------
if (-not (Test-Path "$SimonDir\run.py")) {
    Die "Expected the Simon repo at $SimonDir (with run.py).`n     Clone it first:  git clone <repo-url> $SimonDir"
}
Set-Location $SimonDir

# --- 3. Ollama service ------------------------------------------------------
try {
    $null = Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 3
    Log "Ollama is up."
} catch {
    Log "Starting Ollama..."
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep 4
}

# --- 4. Wizard --------------------------------------------------------------
$wantModels = $true
if (-not (Test-Path ".env")) {
    Log "First-run wizard - writing .env"
    $choice = Ask "Brain: (l)ocal models [free/private] or (c)loud API" "l"
    if ($choice -match "^[cC]") {
        $wantModels = $false
        $base  = Ask "LLM base URL" "https://api.openai.com/v1"
        $key   = Ask "LLM API key" ""
        $model = Ask "Model name" "gpt-4o-mini"
        $fast = ""; $router = "false"
    } else {
        $base = "http://localhost:11434/v1"; $key = ""
        $model = "gpt-oss:20b"; $fast = "qwen3:8b"; $router = "true"
    }
    $tg      = Ask "Telegram bot token (blank = skip)" ""
    $tgIds   = ""; if ($tg) { $tgIds = Ask "Your Telegram user ID (numeric)" "" }
    $slBot   = Ask "Slack bot token xoxb-... (blank = skip)" ""
    $slApp = ""; $slIds = ""
    if ($slBot) { $slApp = Ask "Slack app token xapp-..." ""; $slIds = Ask "Allowed Slack user IDs" "" }
    $license = Ask "License key (blank = free trial)" ""

    Copy-Item .env.example .env
    function Set-KV($k, $v) {
        (Get-Content .env) -replace "^$k=.*", "$k=$v" | Set-Content .env
    }
    Set-KV "LLM_BASE_URL" $base;        Set-KV "LLM_API_KEY" $key
    Set-KV "LLM_MODEL" $model;          Set-KV "LLM_MODEL_FAST" $fast
    Set-KV "LLM_ROUTER_ENABLED" $router
    Set-KV "TELEGRAM_BOT_TOKEN" $tg;    Set-KV "TELEGRAM_ALLOWED_USER_IDS" $tgIds
    Set-KV "SLACK_BOT_TOKEN" $slBot;    Set-KV "SLACK_APP_TOKEN" $slApp
    Set-KV "SLACK_ALLOWED_USER_IDS" $slIds
    Set-KV "SIMON_LICENSE_KEY" $license
    Log ".env written."
} else {
    Log ".env already exists - leaving it untouched."
    if (-not (Select-String -Path .env -Pattern "localhost:11434" -Quiet)) { $wantModels = $false }
}

# --- 5. Model fleet ---------------------------------------------------------
if ($SkipModels -or -not $wantModels) {
    if ($SkipModels) { Warn "Skipping model downloads (-SkipModels)." }
} else {
    foreach ($m in @("gpt-oss:20b", "qwen3:8b", "nomic-embed-text")) {
        if ((ollama list 2>$null) -match [regex]::Escape($m)) {
            Write-Host "    ok: $m already pulled"
        } else {
            Log "Pulling $m (gpt-oss is ~13 GB — the slow part)..."
            ollama pull $m
        }
    }
}

# --- 6. Python venv ---------------------------------------------------------
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Log "Creating virtualenv..."
    python -m venv .venv
}
Log "Installing Python dependencies..."
& .venv\Scripts\pip.exe install --upgrade pip -q
& .venv\Scripts\pip.exe install -q -r requirements.txt

# --- 7. Default mcp.json ----------------------------------------------------
if (-not (Test-Path "mcp.json")) {
    Log "Writing default mcp.json (filesystem tools over .\workspace)..."
    $npx = (Get-Command npx -ErrorAction SilentlyContinue).Source
    if (-not $npx) { $npx = "npx" }
    $ws = "$SimonDir\workspace" -replace '\\', '\\'
    @"
{
  "servers": {
    "filesystem": {
      "command": "$($npx -replace '\\','\\')",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "$ws"],
      "env": {}
    }
  }
}
"@ | Set-Content mcp.json
}
New-Item -ItemType Directory -Force -Path workspace, data\logs | Out-Null

# --- 8. Scheduled Tasks (logon trigger, restart on failure) -----------------
$py = "$SimonDir\.venv\Scripts\python.exe"
foreach ($svc in @(
    @{ Name = "SimonAssistant"; Args = "run.py server" },
    @{ Name = "SimonMonitor";   Args = "-m simon.monitor_app" }
)) {
    Log "Registering scheduled task $($svc.Name) ..."
    $action = New-ScheduledTaskAction -Execute $py -Argument $svc.Args -WorkingDirectory $SimonDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $svc.Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $svc.Name
}

# --- 9. Health check --------------------------------------------------------
Start-Sleep 6
foreach ($probe in @(@("Web UI", 8788), @("Monitor", 8789))) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$($probe[1])/" -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) { Log "$($probe[0]) healthy (port $($probe[1]))." }
    } catch {
        Warn "$($probe[0]) not answering yet - check data\logs\"
    }
}

Write-Host ""
Write-Host "Simon is installed and running." -ForegroundColor Green
Write-Host "  Web chat   http://localhost:8788"
Write-Host "  Monitor    http://localhost:8789"
Write-Host "  Tasks      Get-ScheduledTask -TaskName Simon*"
Write-Host "  Restart    Restart-ScheduledTask is not a thing; use:"
Write-Host "             Stop-ScheduledTask / Start-ScheduledTask -TaskName SimonAssistant"
Write-Host "  Edit .env to add channels later, then restart the task."
