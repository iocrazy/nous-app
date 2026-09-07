# nous-codex one-line installer (Windows / PowerShell).
#
# ⚠️ NOT VERIFIED ON REAL WINDOWS HARDWARE. The macOS/Linux install.sh and the
#    systemd path are the tested ones; this file is the same flow translated,
#    kept here so Windows users have a starting point rather than nothing.
#
#   irm https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.ps1 | iex
#   # or, with the pairing code up front:
#   & ([scriptblock]::Create((irm https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.ps1))) ABCD2345
#   # trailing args are forwarded to `pair`:
#   … ABCD2345 --name "studio pc"
#
# UPGRADING an already-paired machine (no pairing code; the device token is
# kept and the scheduled task is re-registered on the new code):
#   & ([scriptblock]::Create((irm https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.ps1))) -Update
#
# $env:NOUS_API_BASE is honoured while this script runs, but a scheduled task
# carries no environment of its own — set it with `setx NOUS_API_BASE "…"` (or
# System Properties -> Environment Variables) so the background task sees it.

param(
  [string]$PairingCode,
  # Update an already-paired machine: keep the device token, just re-download
  # the daemon and re-register the task. Mirrors `install.sh --update`.
  [switch]$Update,
  # Everything after the code (e.g. --name "studio pc") is forwarded to `pair`.
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
# Make a failing native command (node, npm, schtasks) a terminating error
# instead of something the script sails past. PowerShell 7.3+; older hosts
# ignore it, which is why the $LASTEXITCODE checks below are still here.
$PSNativeCommandUseErrorActionPreference = $true

# Served by nous itself, on both lines the daemon will use; $env:NOUS_API_BASE
# narrows this to one base or a comma list, exactly as the daemon reads it.
$Bases      = if ($env:NOUS_API_BASE) { $env:NOUS_API_BASE } else { 'https://cn.nous.ink:88,https://api.nous.ink' }
$DistPath   = '/api/v1/codex-daemon/dist'
$InstallDir = Join-Path $env:LOCALAPPDATA 'nous-codex'
$Script     = Join-Path $InstallDir 'nous-codex.mjs'
# Must match `xdgConfigHome()` in index.mjs — read only, never written here.
$ConfigHome = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $env:USERPROFILE '.config' }
$ConfigFile = Join-Path (Join-Path $ConfigHome 'nous-codex') 'config.json'

# Is there a pairing worth keeping? `pair` writes device_id + device_token, so
# the token's presence is the same condition `install-service` itself checks.
function Test-Pairing {
  (Test-Path $ConfigFile) -and ((Get-Content $ConfigFile -Raw -ErrorAction SilentlyContinue) -match '"device_token"')
}

# Write-Error is terminating under $ErrorActionPreference = 'Stop', so the
# `exit 1` that used to follow it was unreachable.
function Die($msg) { Write-Error $msg }

function Assert-NativeOk($what) {
  if ($LASTEXITCODE -ne 0) { Die "$what failed (exit $LASTEXITCODE)" }
}

# ── node >= 20 ────────────────────────────────────────────────────────────
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Die "Node.js 20+ is required but 'node' was not found. Install it from https://nodejs.org/en/download and re-run."
}
$nodeMajor = [int](node -p 'process.versions.node.split(".")[0]')
if ($nodeMajor -lt 20) {
  Die "Node.js 20+ is required but this machine has $(node -v). Upgrade from https://nodejs.org/en/download and re-run."
}
Write-Host "node $(node -v) ok"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Die 'npm was not found — it ships with Node.js. Reinstall Node from https://nodejs.org/en/download'
}

# ── the two CLIs the daemon shells out to ─────────────────────────────────
if (Get-Command codex -ErrorAction SilentlyContinue) {
  Write-Host 'codex CLI found'
} else {
  Write-Host 'installing codex CLI (npm i -g @openai/codex) …'
  npm i -g '@openai/codex' | Out-Null
  Assert-NativeOk 'npm i -g @openai/codex'
}
if (Get-Command gpt-image-2-skill -ErrorAction SilentlyContinue) {
  Write-Host 'gpt-image-2-skill found'
} else {
  Write-Host 'installing gpt-image-2-skill (npm i -g gpt-image-2-skill) …'
  npm i -g 'gpt-image-2-skill' | Out-Null
  Assert-NativeOk 'npm i -g gpt-image-2-skill'
}

# ── the daemon itself ─────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host "downloading the daemon to $Script …"
$fetched = $false
foreach ($base in ($Bases -split ',')) {
  $base = $base.Trim().TrimEnd('/')
  if (-not $base) { continue }
  try {
    Invoke-WebRequest -UseBasicParsing -Uri "$base$DistPath/index.mjs" -OutFile $Script
    $fetched = $true
    break
  } catch {
    Write-Host "download failed on $base — trying the next line"
  }
}
if (-not $fetched) { throw "download failed on every line: $Bases" }

# ── codex login (browser OAuth; we cannot do it for you) ──────────────────
$authJson = Join-Path $env:USERPROFILE '.codex\auth.json'
if (-not (Test-Path $authJson)) {
  Write-Host ''
  Write-Host "No codex login found ($authJson)."
  Write-Host 'Run this first, approve in the browser, then re-run this installer:'
  Write-Host ''
  Write-Host '    codex login'
  Write-Host ''
  exit 1
}
Write-Host 'codex login found'

# ── pair (skipped when this is an update) ─────────────────────────────────
#
# An upgrade must NOT re-pair: re-pairing mints a second device row, so the
# honest upgrade keeps the token that is already there. An explicit pairing
# code still wins — that is how you move a machine to a different account.
$UpdateOnly = [bool]$Update
if ($UpdateOnly) {
  if (-not (Test-Pairing)) {
    Die "-Update needs a machine that is already paired, but $ConfigFile holds no device token. Install it fresh instead, with a code from nous -> Settings -> AI -> Local CLI -> Pair a device."
  }
  Write-Host "keeping the existing pairing ($ConfigFile)"
} elseif (-not $PairingCode -and (Test-Pairing)) {
  $UpdateOnly = $true
  Write-Host "already paired ($ConfigFile) — updating in place."
  Write-Host 'Pass a pairing code instead if you meant to re-pair this machine.'
} else {
  if (-not $PairingCode) {
    $PairingCode = Read-Host 'Pairing code (nous → Settings → AI → Local CLI → Pair a device)'
  }
  if (-not $PairingCode) { Die 'no pairing code given. Pass -Update instead to upgrade an already-paired machine.' }

  Write-Host ''
  node $Script pair $PairingCode @RemainingArgs
  Assert-NativeOk 'nous-codex pair'
}

# ── run at logon ──────────────────────────────────────────────────────────
Write-Host ''
node $Script install-service
Assert-NativeOk 'nous-codex install-service'

Write-Host ''
node $Script status
Write-Host ''
if ($UpdateOnly) {
  Write-Host 'Updated. The task was re-registered on the new daemon; the pairing was left alone.'
} else {
  Write-Host 'Done. The device should now appear online in nous → Settings → AI → Local CLI.'
}
Write-Host "To remove it later:  node $Script uninstall-service"
