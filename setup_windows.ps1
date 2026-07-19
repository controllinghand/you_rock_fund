#Requires -Version 5.1
<#
.SYNOPSIS
    DEPRECATED — use setup_docker.sh instead.

    setup_docker.sh now handles Windows (Git Bash), macOS, and Linux with a
    unified flow: secrets container web UI, Task Scheduler auto-start on
    Windows, and launchd on macOS.  Run from Git Bash:

        bash setup_docker.sh --paper

    See WINDOWS_SETUP.md for the full guide.

.SYNOPSIS (original, kept for reference)
    Windows setup script for You Rock Volatility Income Fund (YRVI).
    Mirrors setup_docker.sh for Mac but targets Windows + Rancher Desktop.

.DESCRIPTION
    Runs five steps:
      1. Preflight checks  — docker, repo root, WSL2 path warning
      2. Env file          — copy .env.compose.example if needed, open in Notepad
      3. Secrets           — create docker\secrets\ and prompt for required values
      4. Bind-mount        — optional docker\data\ visibility for logs and state
      5. Containers        — docker compose up -d --build, GO/NO-GO, safety check

.PARAMETER DryRun
    Skip all docker commands. Useful for testing the script flow without
    actually starting containers.

.EXAMPLE
    .\setup_windows.ps1
    .\setup_windows.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Color helpers ─────────────────────────────────────────────────────────────
function ok($msg)   { Write-Host "  [OK]  $msg" -ForegroundColor Green }
function err($msg)  { Write-Host "  [ERR] $msg" -ForegroundColor Red }
function warn($msg) { Write-Host "  [!]   $msg" -ForegroundColor Yellow }
function info($msg) { Write-Host "  [i]   $msg" -ForegroundColor Cyan }

function fail($msg) {
    Write-Host ""
    err $msg
    Write-Host ""
    exit 1
}

function Print-Step([int]$n, [int]$total, [string]$title) {
    Write-Host ""
    Write-Host "Step $n / $total   $title" -ForegroundColor White
    Write-Host ("─" * 54)
}

# ── Banner ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "╔══════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║    Windows Setup — YRVI Fund                     ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {
    warn "DRY-RUN mode — all docker commands will be skipped"
    Write-Host ""
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Preflight checks
# ══════════════════════════════════════════════════════════════════════════════
Print-Step 1 5 "Preflight checks"

# Confirm we are in PowerShell, not a WSL bash session that somehow got here
if (-not $PSVersionTable -or -not $PSVersionTable.PSVersion) {
    fail "This script must be run in PowerShell, not bash or WSL2 bash."
}
ok "Running in PowerShell $($PSVersionTable.PSVersion)"

# Warn if the current directory is on a WSL2 filesystem path.
# Docker bind-mounts from \\wsl$\... are unreliable; the repo should live
# on the Windows filesystem (C:\Users\...).
$cwd = (Get-Location).Path
if ($cwd -match '^\\\\wsl\$' -or $cwd -match '^//wsl\$') {
    Write-Host ""
    warn "You are running from a WSL2 path:"
    warn "  $cwd"
    warn "Docker volume mounts do not work reliably from \\wsl`$\..."
    Write-Host ""
    Write-Host "  Clone the repo into the Windows filesystem instead:" -ForegroundColor Yellow
    Write-Host "    cd C:\Users\$env:USERNAME" -ForegroundColor Yellow
    Write-Host "    git clone https://github.com/controllinghand/you_rock_fund.git" -ForegroundColor Yellow
    Write-Host "    cd you_rock_fund" -ForegroundColor Yellow
    Write-Host "    .\setup_windows.ps1" -ForegroundColor Yellow
    Write-Host ""
    $cont = Read-Host "Continue from WSL2 path anyway? (y/N)"
    if ($cont -ne 'y' -and $cont -ne 'Y') { exit 1 }
}

# Check that git is available in PATH
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "  ===================================================" -ForegroundColor Red
    Write-Host "  Git is not installed or not in your PATH." -ForegroundColor Red
    Write-Host "  Install Git for Windows before continuing:" -ForegroundColor Red
    Write-Host "  https://git-scm.com/download/win" -ForegroundColor Red
    Write-Host "  During install, choose:" -ForegroundColor Red
    Write-Host "    - 'Git from the command line and also from" -ForegroundColor Red
    Write-Host "       3rd-party software' (adds git to PATH)" -ForegroundColor Red
    Write-Host "  After install, close and reopen PowerShell," -ForegroundColor Red
    Write-Host "  then rerun this script." -ForegroundColor Red
    Write-Host "  ===================================================" -ForegroundColor Red
    Write-Host ""
    exit 1
}
$gitVersion = (& git --version 2>&1) -join ''
ok "Git found  ($gitVersion)"

# Confirm we are in the repo root
if (-not (Test-Path "docker-compose.yml")) {
    fail ("docker-compose.yml not found.`n" +
          "       Run this script from the repo root:`n" +
          "         cd C:\Users\$env:USERNAME\you_rock_fund`n" +
          "         .\setup_windows.ps1")
}
ok "Repo root confirmed (docker-compose.yml present)"

# Check that docker is available in PATH
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host ""
    err "docker not found in PATH."
    Write-Host ""
    Write-Host "  Install Rancher Desktop and enable dockerd (moby):" -ForegroundColor Yellow
    Write-Host "    1. Download from https://rancherdesktop.io" -ForegroundColor Yellow
    Write-Host "    2. Open Rancher Desktop → Preferences → Container Engine" -ForegroundColor Yellow
    Write-Host "    3. Select  dockerd (moby)  — not containerd" -ForegroundColor Yellow
    Write-Host "    4. Click Apply and wait for the status indicator to show Running" -ForegroundColor Yellow
    Write-Host "    5. Open a new PowerShell window and rerun this script" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# Confirm the Docker daemon is actually responding
$dockerInfo = & docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    err "Docker daemon is not responding (exit code $LASTEXITCODE)."
    Write-Host ""
    Write-Host "  Make sure Rancher Desktop is open and its status indicator" -ForegroundColor Yellow
    Write-Host "  shows  Running  before retrying." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$dockerVersion = (& docker version --format '{{.Server.Version}}' 2>&1) -join ''
ok "Docker running  (server $dockerVersion)"

# Check docker compose V2 plugin (Rancher Desktop bundles it with dockerd)
& docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    fail ("docker compose (V2) not found.`n" +
          "       Rancher Desktop with dockerd (moby) includes it.`n" +
          "       Reinstall or update Rancher Desktop and retry.")
}
ok "docker compose (V2) available"

Write-Host ""
warn "Tip: configure Rancher Desktop to auto-start so Docker is"
warn "     running after every Windows reboot:"
Write-Host "       Rancher Desktop → Preferences → Application →" -ForegroundColor Yellow
Write-Host "         ✅  Automatically start at login" -ForegroundColor Yellow
Write-Host "         ✅  Start in background" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Environment file (.env.compose)
# ══════════════════════════════════════════════════════════════════════════════
Print-Step 2 5 "Environment file (.env.compose)"

if (-not (Test-Path ".env.compose")) {
    if (-not (Test-Path ".env.compose.example")) {
        fail ".env.compose.example not found — check you are in the repo root."
    }
    Copy-Item ".env.compose.example" ".env.compose"
    ok "Copied .env.compose.example → .env.compose"
} else {
    ok ".env.compose already exists"
}

Write-Host ""
info "Opening .env.compose in Notepad. Fill in at minimum:"
Write-Host "    ACCOUNT_PAPER=DUP...     (your IBKR paper account ID)" -ForegroundColor Cyan
Write-Host "    TWS_USERID_PAPER=...     (your IBKR paper login username)" -ForegroundColor Cyan
Write-Host ""
info "Save the file and close Notepad when finished."
Write-Host ""

$envFilePath = Join-Path (Get-Location) ".env.compose"
Start-Process "notepad.exe" -ArgumentList "`"$envFilePath`"" -Wait

Write-Host ""
Read-Host "Press Enter to continue after saving .env.compose"

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Secrets (docker\secrets\)
# ══════════════════════════════════════════════════════════════════════════════
Print-Step 3 5 "Secrets (docker\secrets\)"

$secretsDir = Join-Path (Get-Location) "docker\secrets"
if (-not (Test-Path $secretsDir)) {
    New-Item -ItemType Directory -Path $secretsDir | Out-Null
    ok "Created $secretsDir"
} else {
    ok "$secretsDir already exists"
}

# Write a string to a file with no trailing newline and no BOM.
# Uses WriteAllBytes so the file is byte-for-byte identical to what
# the Mac 'printf' approach produces.
function Write-SecretFile([string]$filePath, [string]$value) {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($value)
    [System.IO.File]::WriteAllBytes($filePath, $bytes)
}

# Convert a SecureString to plain text without leaving a copy on the heap.
# Compatible with both PowerShell 5.1 (.NET Framework) and PS 7+ (.NET Core).
function ConvertTo-PlainText([System.Security.SecureString]$secure) {
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

# Only the two required secrets are prompted here. Optional secrets
# (discord_webhook_url, anthropic_api_key, tws_password_live, etc.) can be
# set later by writing values directly into docker\secrets\.
$requiredSecrets = @(
    [PSCustomObject]@{
        File   = "tws_password_paper"
        Prompt = "IBKR paper trading password (for TWS_USERID_PAPER)"
    },
    [PSCustomObject]@{
        File   = "render_secret"
        Prompt = "You Rock Club Render screener API secret"
    }
)

foreach ($secret in $requiredSecrets) {
    $secretPath = Join-Path $secretsDir $secret.File

    $hasContent = (Test-Path $secretPath) -and ((Get-Item $secretPath).Length -gt 0)

    if ($hasContent) {
        ok "$($secret.File) — already set, skipping"
    } else {
        Write-Host ""
        info "Required secret: $($secret.File)"
        info "  $($secret.Prompt)"
        $secure = Read-Host "  Enter value" -AsSecureString
        $plain  = ConvertTo-PlainText $secure

        if ([string]::IsNullOrWhiteSpace($plain)) {
            warn "$($secret.File) left empty — the stack will not connect until this is set."
            warn "  Write the value to: $secretPath"
        } else {
            Write-SecretFile $secretPath $plain
            ok "$($secret.File) saved"
        }

        # Overwrite the plain-text variable before it gets GC'd
        $plain = $null
    }
}

Write-Host ""
ok "Secrets step complete"

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Bind-mount override
#
# By default, runtime files (state.json, trade_log.txt, scheduler_log.txt,
# etc.) live inside a Docker named volume and are only reachable via
# 'docker compose exec' or 'docker compose logs'.  The bind-mount override
# copies docker-compose.override.yml.example into place so that those same
# files are also written to docker\data\ on the Windows filesystem, where
# you can open them in Explorer, Notepad, or VS Code.
# ══════════════════════════════════════════════════════════════════════════════
Print-Step 4 5 "Bind-mount override (docker\data\ visibility)"

Write-Host ""
Write-Host "  By default, runtime logs and data (state.json, trade_log.txt," -ForegroundColor White
Write-Host "  etc.) live inside a Docker named volume and are only accessible" -ForegroundColor White
Write-Host "  via 'docker compose exec' or 'docker compose logs'." -ForegroundColor White
Write-Host ""
Write-Host "  With the bind-mount override, those files are also written to" -ForegroundColor White
Write-Host "  docker\data\ on your Windows filesystem — open them directly in" -ForegroundColor White
Write-Host "  Explorer, Notepad, or VS Code without using docker exec." -ForegroundColor White
Write-Host ""

$bindChoice = Read-Host "  Enable bind-mount (files visible in docker\data\)? [Y/N]"

if ($bindChoice -eq 'Y' -or $bindChoice -eq 'y') {
    $overrideDst = Join-Path (Get-Location) "docker-compose.override.yml"
    $overrideSrc = Join-Path (Get-Location) "docker-compose.override.yml.example"

    if (Test-Path $overrideDst) {
        warn "docker-compose.override.yml already exists — skipping copy"
        warn "  Delete it and rerun this step if you want to reset the override."
    } elseif (-not (Test-Path $overrideSrc)) {
        warn "docker-compose.override.yml.example not found — skipping bind-mount setup."
        warn "  You can set this up manually later; see CONTAINERIZATION.md."
    } else {
        Copy-Item $overrideSrc $overrideDst
        $dataDir = Join-Path (Get-Location) "docker\data"
        if (-not (Test-Path $dataDir)) {
            New-Item -ItemType Directory -Path $dataDir | Out-Null
        }
        ok "Bind-mount override enabled — runtime files will be visible in docker\data\"
    }
} else {
    Write-Host ""
    warn "Bind-mount skipped. Logs are still accessible via:"
    Write-Host "    docker compose logs -f scheduler" -ForegroundColor Yellow
    Write-Host "    docker compose logs -f api" -ForegroundColor Yellow
}

# ══════════════════════════════════════════════════════════════════════════════
# Step 5: Build and start containers
# ══════════════════════════════════════════════════════════════════════════════
Print-Step 5 5 "Build and start all 4 containers"

if ($DryRun) {
    warn "DRY-RUN: skipping 'docker compose up -d --build'"
    warn "DRY-RUN: skipping 'docker compose ps'"
    warn "DRY-RUN: skipping safety check"
} else {
    info "Building images and starting ib_gateway, api, scheduler, web..."
    Write-Host ""


    # .env.compose is install-time only (host ports + image tag) and is OPTIONAL:
    # everything it can set has a compose default. `docker compose --env-file
    # <missing>` is a hard error, so only pass the flag when the file is present.
    $EnvFileArgs = @()
    if (Test-Path ".env.compose") { $EnvFileArgs = @("--env-file", ".env.compose") }

    # Stream docker compose output directly to the terminal so the user can
    # watch image pulls and build layers as they happen.
    & docker compose @EnvFileArgs up -d --build
    if ($LASTEXITCODE -ne 0) {
        fail "'docker compose up' exited with code $LASTEXITCODE — see output above."
    }

    # Brief pause to let containers reach a stable state before checking status
    Start-Sleep -Seconds 3

    Write-Host ""
    info "Current container status:"
    Write-Host ""
    & docker compose @EnvFileArgs ps
    Write-Host ""

    # Count lines that indicate a container is running
    $psLines = & docker compose @EnvFileArgs ps 2>&1
    $runningCount = ($psLines | Where-Object { $_ -match '\b(Up|running|healthy)\b' } |
                     Measure-Object).Count

    # GO / NO-GO verdict table
    Write-Host "  ┌──────────────────────────────────────────┐"
    if ($runningCount -ge 4) {
        Write-Host ("  │  GO     — {0} / 4 containers running        │" -f $runningCount) -ForegroundColor Green
    } elseif ($runningCount -gt 0) {
        Write-Host ("  │  WAIT   — {0} / 4 containers running        │" -f $runningCount) -ForegroundColor Yellow
        Write-Host  "  │  IB Gateway may still be initializing   │" -ForegroundColor Yellow
        Write-Host  "  │  Allow ~60 s then check logs below      │" -ForegroundColor Yellow
    } else {
        Write-Host  "  │  NO-GO  — no containers appear running  │" -ForegroundColor Red
        Write-Host  "  │  Check output above and logs below      │" -ForegroundColor Red
    }
    Write-Host "  └──────────────────────────────────────────┘"

    # ── Safety check: confirm dry_run default ─────────────────────────────────
    # On a fresh data volume, settings.json is initialized from
    # settings_default.json with dry_run=true so YRVI cannot submit orders
    # until the user consciously enables it.  This check surfaces that state
    # immediately rather than leaving it as a silent assumption.
    Write-Host ""
    info "Waiting 15 s for API container to initialize..."
    for ($i = 15; $i -ge 1; $i--) {
        Write-Host -NoNewline "`r  [i]   $i s remaining...  "
        Start-Sleep -Seconds 1
    }
    Write-Host "`r  [i]   Checking API...    "
    Write-Host ""

    try {
        $settings = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/settings" `
                        -TimeoutSec 5 -ErrorAction Stop

        if ($settings.dry_run -eq $true) {
            Write-Host "  ===================================================" -ForegroundColor Green
            Write-Host "  SAFETY CHECK: dry_run = TRUE" -ForegroundColor Green
            Write-Host "  YRVI will NOT submit real orders to IBKR" -ForegroundColor Green
            Write-Host "  This is the correct default for a new install" -ForegroundColor Green
            Write-Host "  When ready to place paper trades run:" -ForegroundColor Green
            Write-Host "    curl -X POST http://127.0.0.1:3000/api/settings" -ForegroundColor Green
            Write-Host "         -H 'Content-Type: application/json'" -ForegroundColor Green
            Write-Host "         -d '{""dry_run"":false}'" -ForegroundColor Green
            Write-Host "  ===================================================" -ForegroundColor Green
        } else {
            Write-Host "  ===================================================" -ForegroundColor Red
            Write-Host "  WARNING: dry_run = FALSE" -ForegroundColor Red
            Write-Host "  YRVI may submit real orders to IBKR" -ForegroundColor Red
            Write-Host "  Verify trading_mode=paper before proceeding" -ForegroundColor Red
            Write-Host "  ===================================================" -ForegroundColor Red
        }
    } catch {
        Write-Host ""
        warn "API not reachable yet — containers may still be starting."
        warn "  IB Gateway takes 30–90 s to log in; the API follows shortly after."
        warn "  Once the stack is up, verify dry_run manually:"
        Write-Host "    curl http://127.0.0.1:8000/api/settings" -ForegroundColor Yellow
        Write-Host "  Expected on a fresh install: dry_run = true" -ForegroundColor Yellow
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# Final instructions
# ══════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host ("═" * 54)
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host ("═" * 54)
Write-Host ""
Write-Host "  Dashboard:   http://localhost:3000"
Write-Host "  API status:  http://localhost:8000/api/status"
Write-Host ""
Write-Host "  Watch IB Gateway log in (takes 30–90 s):"
Write-Host "    docker compose logs -f ib_gateway" -ForegroundColor Cyan
Write-Host "  Look for: 'Login has completed'"
Write-Host ""
Write-Host "  Other useful commands:"
Write-Host "    docker compose logs -f api" -ForegroundColor Cyan
Write-Host "    docker compose logs -f scheduler" -ForegroundColor Cyan
Write-Host "    docker compose ps" -ForegroundColor Cyan
Write-Host ""

# Viewing the Gateway is informational — most first-time logins complete automatically
Write-Host "  ==================================================="
Write-Host "  2FA / VIEWING THE IB GATEWAY SCREEN:"
Write-Host "  Most first-time logins complete automatically."
Write-Host "  If IBKR requires 2FA or shows a login dialog,"
Write-Host "  use the built-in View Gateway viewer:"
Write-Host "    Dashboard -> Help -> System Diagnostics -> View Gateway"
Write-Host "  No VNC client to install; the password auto-fills."
Write-Host "  Details: docs/view-gateway.md"
Write-Host "  Most users won't need this on first install."
Write-Host "  ==================================================="

Write-Host ""
Write-Host "  Always run docker compose commands from PowerShell" -ForegroundColor Yellow
Write-Host "  in C:\Users\... — not from WSL2/Ubuntu terminal." -ForegroundColor Yellow
Write-Host ""
