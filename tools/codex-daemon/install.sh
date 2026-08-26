#!/bin/sh
# nous-codex one-line installer (macOS / Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/install.sh | sh -s -- <PAIRING-CODE>
#
# Installs the two CLIs if missing, drops the daemon in
# ~/.local/share/nous-codex/, pairs it with your nous account, and registers
# it as a login service so it survives closing the terminal and rebooting.
#
# NOUS_API_BASE is honoured and carried into the service unit.
set -eu

RAW_URL="https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs"
INSTALL_DIR="${HOME}/.local/share/nous-codex"
SCRIPT="${INSTALL_DIR}/nous-codex.mjs"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# ── node ≥ 20 ─────────────────────────────────────────────────────────────
command -v node >/dev/null 2>&1 || die "Node.js 20+ is required but 'node' was not found.
Install it from https://nodejs.org/en/download (or: brew install node / your distro's package manager), then re-run this script."

NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
[ "$NODE_MAJOR" -ge 20 ] 2>/dev/null || die "Node.js 20+ is required but this machine has $(node -v).
Upgrade from https://nodejs.org/en/download, then re-run this script."
say "node $(node -v) ok"

# ── the two CLIs the daemon shells out to ─────────────────────────────────
install_npm_global() {
  say "installing $1 (npm i -g $2) …"
  npm i -g "$2" >/dev/null || die "npm i -g $2 failed. Run it yourself (you may need sudo) and re-run this script."
}

command -v npm >/dev/null 2>&1 || die "npm was not found — it ships with Node.js. Reinstall Node from https://nodejs.org/en/download"

if command -v codex >/dev/null 2>&1; then
  say "codex CLI found"
else
  install_npm_global "codex CLI" "@openai/codex"
fi

if command -v gpt-image-2-skill >/dev/null 2>&1; then
  say "gpt-image-2-skill found"
else
  install_npm_global "gpt-image-2-skill" "gpt-image-2-skill"
fi

# ── the daemon itself ─────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
say "downloading the daemon to ${SCRIPT} …"
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$RAW_URL" -o "$SCRIPT" || die "download failed: $RAW_URL"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$SCRIPT" "$RAW_URL" || die "download failed: $RAW_URL"
else
  die "neither curl nor wget is available — cannot download $RAW_URL"
fi
chmod 0755 "$SCRIPT"

# ── codex login (browser OAuth; we cannot do it for you) ──────────────────
if [ ! -f "${HOME}/.codex/auth.json" ]; then
  say ""
  say "No codex login found (${HOME}/.codex/auth.json)."
  say "Run this first, approve in the browser, then re-run this installer:"
  say ""
  say "    codex login"
  say ""
  exit 1
fi
say "codex login found"

# ── pair ──────────────────────────────────────────────────────────────────
CODE="${1-}"
if [ -z "$CODE" ]; then
  # `curl … | sh` leaves stdin as the pipe, so read from the terminal.
  if [ -r /dev/tty ]; then
    printf 'Pairing code (nous → Settings → AI → Local CLI → Pair a device): '
    read -r CODE </dev/tty
  else
    die "no pairing code given. Usage: install.sh <PAIRING-CODE>"
  fi
fi
[ -n "$CODE" ] || die "no pairing code given. Usage: install.sh <PAIRING-CODE>"

say ""
node "$SCRIPT" pair "$CODE"

# ── run at login, restart on crash ────────────────────────────────────────
say ""
node "$SCRIPT" install-service

# ── report ────────────────────────────────────────────────────────────────
say ""
node "$SCRIPT" status
say ""
say "Done. The device should now appear online in nous → Settings → AI → Local CLI."
say "To remove it later:  node ${SCRIPT} uninstall-service"
