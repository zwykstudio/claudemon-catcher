#
# Claudemon statusline for Claude Code (Windows)
#
# Reads ~/.claudemon/statusline.json (written by the engine after each catch)
# and displays a one-liner in the Claude Code status bar.
#
# Install:
#   claude config set statusline "powershell -NoProfile -File C:\path\to\statusline.ps1"
#

$StatusFile = Join-Path $env:USERPROFILE ".claudemon\statusline.json"

# Read Claude Code session data from stdin
$input = $Input | Out-String

$model = ""
$pct = ""
try {
    $session = $input | ConvertFrom-Json -ErrorAction SilentlyContinue
    if ($session.model.display_name) { $model = $session.model.display_name }
    if ($session.context_window.used_percentage) {
        $pct = [math]::Floor($session.context_window.used_percentage)
    }
} catch {}

$base = ""
if ($model) { $base = $model }
if ($pct -and $pct -ne "0") { $base = "$base ${pct}%" }

# No statusline file — engine hasn't caught anything
if (-not (Test-Path $StatusFile)) {
    Write-Output $base
    exit 0
}

try {
    $data = Get-Content $StatusFile -Raw | ConvertFrom-Json
} catch {
    Write-Output $base
    exit 0
}

$word = $data.word
$isNew = $data.is_new
$level = $data.level
$evolved = $data.evolved
$hatched = $data.just_hatched
$count = $data.session_catches
$ts = $data.ts

# Check staleness — hide after 5 minutes
$now = [int][double]::Parse((Get-Date -UFormat %s))
$age = $now - [int][double]::Parse($ts.ToString())
if ($age -gt 300) {
    Write-Output $base
    exit 0
}

# Build catch message
if ($isNew -eq $true) {
    $msg = "new egg!"
} elseif ($hatched -eq $true) {
    $msg = "hatched!"
} elseif ($evolved -eq $true) {
    $msg = "evolved!"
} else {
    $msg = "lv.${level}"
}

Write-Output "$base | ♦ ${word} — ${msg} (${count} caught)"
