# nous-codex one-line installer (Windows / PowerShell).
#
# ⚠️ NOT VERIFIED ON REAL WINDOWS HARDWARE. The macOS/Linux install.sh and the
#    systemd path are the tested ones; this file is the same flow translated,
#    kept here so Windows users have a starting point rather than nothing.
#
#   irm https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/install.ps1 | iex
#   # or, with the pairing code up front:
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/install.ps1))) ABCD2345
#
# $env:NOUS_API_BASE is honoured and carried into the scheduled task's env.

param([string]$PairingCode)

$ErrorActionPreference = 'Stop'

$RawUrl     = 'https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs'
$InstallDir = Join-Path $env:LOCALAPPDATA 'nous-codex'
$Script     = Join-Path $InstallDir 'nous-codex.mjs'

function Die($msg) { Write-Error $msg; exit 1 }

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
}
if (Get-Command gpt-image-2-skill -ErrorAction SilentlyContinue) {
  Write-Host 'gpt-image-2-skill found'
} else {
  Write-Host 'installing gpt-image-2-skill (npm i -g gpt-image-2-skill) …'
  npm i -g 'gpt-image-2-skill' | Out-Null
}

# ── the daemon itself ─────────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Write-Host "downloading the daemon to $Script …"
Invoke-WebRequest -UseBasicParsing -Uri $RawUrl -OutFile $Script

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

# ── pair ──────────────────────────────────────────────────────────────────
if (-not $PairingCode) {
  $PairingCode = Read-Host 'Pairing code (nous → Settings → AI → Local CLI → Pair a device)'
}
if (-not $PairingCode) { Die 'no pairing code given.' }

Write-Host ''
node $Script pair $PairingCode

# ── run at logon ──────────────────────────────────────────────────────────
Write-Host ''
node $Script install-service

Write-Host ''
node $Script status
Write-Host ''
Write-Host 'Done. The device should now appear online in nous → Settings → AI → Local CLI.'
Write-Host "To remove it later:  node $Script uninstall-service"
