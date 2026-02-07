#Requires -Version 5.1
<#
.SYNOPSIS
    Claudemon Setup Script (Windows)

.DESCRIPTION
    1. Checks prerequisites (Python, pywinpty, Claude CLI)
    2. Configures API key or local mode
    3. Installs engine daemon (Task Scheduler)
    4. Creates cc.cmd wrapper and adds to PATH

.EXAMPLE
    .\install\setup.ps1
    # Or with env vars pre-set:
    $env:CLAUDEMON_API_KEY = "sk_claudemon_..."
    $env:CLAUDEMON_CLOUD_URL = "http://localhost:3000"
    .\install\setup.ps1
#>

$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$BaseDir = Split-Path -Parent $ScriptDir
$AppDir = Join-Path $env:LOCALAPPDATA "Claudemon"

Write-Host ""
Write-Host "  ╔════════════════════════════════════════╗"
Write-Host "  ║          CLAUDEMON SETUP               ║"
Write-Host "  ╚════════════════════════════════════════╝"
Write-Host ""

# ── Prerequisites ──

Write-Host "  → Checking prerequisites..."

# Find Python
$PythonCmd = $null
foreach ($cmd in @("python3", "python", "py")) {
    $found = Get-Command $cmd -ErrorAction SilentlyContinue
    if ($found) {
        $version = & $found.Source --version 2>&1
        if ($version -match "Python 3\.") {
            $PythonCmd = $found.Source
            break
        }
    }
}
if (-not $PythonCmd) {
    Write-Host "    ✗ Python 3 not found. Install from https://python.org"
    exit 1
}
Write-Host "    ✓ Python 3 found: $PythonCmd"

# Check/install pywinpty
$hasPywinpty = & $PythonCmd -c "import winpty" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "    Installing pywinpty..."
    & $PythonCmd -m pip install pywinpty -q 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    ✗ Failed to install pywinpty. Run: pip install pywinpty"
        exit 1
    }
    Write-Host "    ✓ pywinpty installed"
} else {
    Write-Host "    ✓ pywinpty found"
}

# Check Claude CLI
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    Write-Host "    ✗ Claude CLI not found."
    Write-Host "      Install: npm install -g @anthropic-ai/claude-code"
    exit 1
}
Write-Host "    ✓ Claude CLI found"
Write-Host ""

# ── Configuration ──

Write-Host "  → Configuration..."

$ApiKey = $env:CLAUDEMON_API_KEY
$Mode = $env:CLAUDEMON_MODE
$CloudUrl = $env:CLAUDEMON_CLOUD_URL
$EnvMode = ""

if ($ApiKey) {
    $EnvMode = "cloud"
    Write-Host "    ✓ CLAUDEMON_API_KEY detected (cloud mode)"
} elseif ($Mode -eq "local") {
    $EnvMode = "local"
    Write-Host "    ✓ CLAUDEMON_MODE=local (local mode)"
} else {
    Write-Host ""
    Write-Host "    No configuration found. Choose a mode:"
    Write-Host ""
    Write-Host "      1) Cloud (default) — sync to https://claudemon.zwyk-studio.com"
    Write-Host "      2) Local — SQLite only, no cloud sync"
    Write-Host ""
    $choice = Read-Host "    Choice [1]"
    if (-not $choice) { $choice = "1" }

    if ($choice -eq "2") {
        $EnvMode = "local"
        $Mode = "local"
        Write-Host "    ✓ Local mode selected"
    } else {
        Write-Host ""
        Write-Host "    Paste your API key (get one at https://claudemon.zwyk-studio.com/dashboard/settings):"
        $InputKey = Read-Host "    API key"
        $InputKey = $InputKey.Trim()
        if (-not $InputKey) {
            Write-Host "    ✗ No key provided. Re-run setup when you have your key."
            exit 1
        }
        if (-not $InputKey.StartsWith("sk_claudemon_")) {
            Write-Host "    ✗ Invalid key. It should start with sk_claudemon_"
            exit 1
        }
        $ApiKey = $InputKey
        $EnvMode = "cloud"
        Write-Host "    ✓ API key set (cloud mode)"
    }
}

if ($CloudUrl) {
    Write-Host "    ✓ Custom cloud URL: $CloudUrl"
}
Write-Host ""

# ── Persist env vars (user-level, survives reboots) ──

Write-Host "  → Setting environment variables..."

if ($EnvMode -eq "cloud") {
    [Environment]::SetEnvironmentVariable("CLAUDEMON_API_KEY", $ApiKey, "User")
    $env:CLAUDEMON_API_KEY = $ApiKey
    # Clean up local mode var if switching
    [Environment]::SetEnvironmentVariable("CLAUDEMON_MODE", $null, "User")
    Write-Host "    ✓ CLAUDEMON_API_KEY saved"
} else {
    [Environment]::SetEnvironmentVariable("CLAUDEMON_MODE", "local", "User")
    $env:CLAUDEMON_MODE = "local"
    # Clean up cloud var if switching
    [Environment]::SetEnvironmentVariable("CLAUDEMON_API_KEY", $null, "User")
    Write-Host "    ✓ CLAUDEMON_MODE=local saved"
}

if ($CloudUrl) {
    [Environment]::SetEnvironmentVariable("CLAUDEMON_CLOUD_URL", $CloudUrl, "User")
    $env:CLAUDEMON_CLOUD_URL = $CloudUrl
    Write-Host "    ✓ CLAUDEMON_CLOUD_URL saved"
}
Write-Host ""

# ── Create cc.cmd wrapper ──

Write-Host "  → Creating cc command..."

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

$ccCmd = Join-Path $AppDir "cc.cmd"
@"
@echo off
"$PythonCmd" "$BaseDir\wrapper.py" %*
"@ | Set-Content -Path $ccCmd -Encoding ASCII

# Add to user PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (-not $userPath) { $userPath = "" }
if ($userPath -notlike "*$AppDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$AppDir", "User")
    $env:PATH = "$env:PATH;$AppDir"
    Write-Host "    ✓ Added $AppDir to PATH"
} else {
    Write-Host "    ✓ Already in PATH"
}
Write-Host "    ✓ cc.cmd created"
Write-Host ""

# ── Engine daemon (Task Scheduler) ──

Write-Host "  → Setting up engine daemon..."

$TaskName = "ClaudemonEngine"

# Build engine launcher script with env vars baked in
$engineCmd = Join-Path $AppDir "engine-start.cmd"
$envLines = ""
if ($EnvMode -eq "cloud") {
    $envLines += "set `"CLAUDEMON_API_KEY=$ApiKey`"`r`n"
} else {
    $envLines += "set `"CLAUDEMON_MODE=local`"`r`n"
}
if ($CloudUrl) {
    $envLines += "set `"CLAUDEMON_CLOUD_URL=$CloudUrl`"`r`n"
}

@"
@echo off
$envLines"$PythonCmd" "$BaseDir\engine\engine.py"
"@ | Set-Content -Path $engineCmd -Encoding ASCII

# Remove existing task if present
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "    Replaced existing task"
}

# Create scheduled task
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$engineCmd`"" `
    -WorkingDirectory $BaseDir

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Claudemon Engine — watches catches and syncs to storage" | Out-Null

# Start it now
Start-ScheduledTask -TaskName $TaskName
Write-Host "    ✓ Engine daemon installed (Task Scheduler)"
Write-Host ""

# ── Done ──

Write-Host "  ╔════════════════════════════════════════╗"
Write-Host "  ║            SETUP COMPLETE              ║"
Write-Host "  ╚════════════════════════════════════════╝"
Write-Host ""
Write-Host "    Dashboard: https://claudemon.zwyk-studio.com/dashboard"
Write-Host "    CLI:       cc [args...]"
Write-Host "    Open:      cc --dashboard"
Write-Host ""
Write-Host "    Open a NEW terminal for PATH changes to take effect."
Write-Host ""
Write-Host "    To stop the engine:"
Write-Host "      Stop-ScheduledTask -TaskName ClaudemonEngine"
Write-Host "    To remove:"
Write-Host "      Unregister-ScheduledTask -TaskName ClaudemonEngine"
Write-Host ""
