#!/bin/sh
# nous-codex one-line installer (macOS / Linux).
#
#   curl -fsSL https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.sh | sh -s -- <PAIRING-CODE>
#
# Anything after the pairing code is forwarded to `pair`, so this works too:
#   … | sh -s -- <PAIRING-CODE> --name "studio mac"
#
# UPGRADING an already-paired machine (no pairing code needed — the existing
# device token is kept and the service is restarted on the new code):
#   curl -fsSL https://cn.nous.ink:88/api/v1/codex-daemon/dist/install.sh | sh -s -- --update
#
# Running it with no arguments at all does the same thing when this machine is
# already paired; it only asks for a pairing code when there is nothing to keep.
#
# Installs the two CLIs if missing, drops the daemon in
# ~/.local/share/nous-codex/, pairs it with your nous account, and registers
# it as a login service so it survives closing the terminal and rebooting.
#
# NOUS_API_BASE is honoured and carried into the service unit.
set -eu

# The daemon is served by nous itself, on both lines the daemon will later use
# (cn direct first, the Cloudflare tunnel second). NOUS_API_BASE narrows this to
# one base, or a comma list, exactly as the daemon reads it.
BASES="${NOUS_API_BASE:-https://cn.nous.ink:88,https://api.nous.ink}"
DIST_PATH="/api/v1/codex-daemon/dist"
INSTALL_DIR="${HOME}/.local/share/nous-codex"
SCRIPT="${INSTALL_DIR}/nous-codex.mjs"
# Must match `xdgConfigHome()` in index.mjs — this is only read, never written.
CONFIG_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/nous-codex/config.json"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Is there a pairing worth keeping? `pair` writes device_id + device_token, so
# the token's presence is the same condition `install-service` itself checks.
has_pairing() { grep -q '"device_token"' "$CONFIG_FILE" 2>/dev/null; }

UPDATE_ONLY=0
if [ "${1-}" = "--update" ]; then
  UPDATE_ONLY=1
  shift
fi

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
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 \
  || die "neither curl nor wget is available — cannot download the daemon"
fetched=0
for base in $(printf '%s' "$BASES" | tr ',' ' '); do
  base="${base%/}"
  url="${base}${DIST_PATH}/index.mjs"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$SCRIPT" && fetched=1 && break
  else
    wget -qO "$SCRIPT" "$url" && fetched=1 && break
  fi
  say "download failed on $base — trying the next line"
done
[ "$fetched" -eq 1 ] || die "download failed on every line: $BASES"
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

# ── pair (skipped when this is an update) ─────────────────────────────────
#
# An upgrade must NOT re-pair: re-pairing mints a second device row and
# invalidates nothing, so the honest upgrade keeps the token that is already
# there. A pairing code given explicitly still wins — that is how you move a
# machine to a different account.
CODE="${1-}"
# Everything after the code (e.g. --name "studio mac") is forwarded to `pair`.
[ "$#" -gt 0 ] && shift

if [ "$UPDATE_ONLY" -eq 1 ]; then
  has_pairing || die "--update needs a machine that is already paired, but ${CONFIG_FILE} holds no device token.
Install it fresh instead, with a code from nous → Settings → AI → Local CLI → Pair a device:
    curl -fsSL ${BASES%%,*}${DIST_PATH}/install.sh | sh -s -- <PAIRING-CODE>"
  say "keeping the existing pairing (${CONFIG_FILE})"
elif [ -z "$CODE" ] && has_pairing; then
  # Already paired and nothing was asked for: this is an upgrade.
  UPDATE_ONLY=1
  say "already paired (${CONFIG_FILE}) — updating in place."
  say "Pass a pairing code instead if you meant to re-pair this machine."
else
  if [ -z "$CODE" ]; then
    # `curl … | sh` leaves stdin as the pipe, so read from the terminal.
    if [ -r /dev/tty ]; then
      printf 'Pairing code (nous → Settings → AI → Local CLI → Pair a device): '
      read -r CODE </dev/tty
    else
      die "no pairing code given. Usage: install.sh <PAIRING-CODE>   (or install.sh --update to upgrade an already-paired machine)"
    fi
  fi
  [ -n "$CODE" ] || die "no pairing code given. Usage: install.sh <PAIRING-CODE>   (or install.sh --update to upgrade an already-paired machine)"

  say ""
  node "$SCRIPT" pair "$CODE" "$@"
fi

# ── run at login, restart on crash ────────────────────────────────────────
say ""
node "$SCRIPT" install-service

# ── report ────────────────────────────────────────────────────────────────
say ""
node "$SCRIPT" status
say ""
if [ "$UPDATE_ONLY" -eq 1 ]; then
  say "Updated. The service was restarted on the new daemon; the pairing was left alone."
else
  say "Done. The device should now appear online in nous → Settings → AI → Local CLI."
fi
say "To remove it later:  node ${SCRIPT} uninstall-service"
