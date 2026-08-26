# nous-codex

Run nous canvas generations **on your own machine, with your own codex login**.

Your codex credentials never leave your computer. nous only sends job
descriptions over an outbound connection; this program runs the CLI locally
and uploads the finished file back.

## Requirements

- Node.js 20+
- A ChatGPT subscription (the whole point: generations spend YOUR quota)
- `codex login` done once (browser OAuth; credentials stay in `~/.codex/auth.json`)

## Install (one line)

1. In nous: **Settings → AI → Local CLI → Pair a device** → copy the 8-character code.
2. On the machine you want to pair (macOS / Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/install.sh | sh -s -- ABCD2345
```

That installs the two CLIs if they are missing, drops the daemon in
`~/.local/share/nous-codex/nous-codex.mjs`, pairs it, and **registers it as a
login service** — so it survives closing the terminal and comes back after a
reboot. It ends by printing `status`.

If `codex login` has not been done yet the installer stops and tells you to
run it first; re-run the installer afterwards.

Windows: `install.ps1` is the same flow in PowerShell, but is **not verified
on real hardware** — see "Platform support" below.

### Manual install (if you'd rather not pipe a script to a shell)

```bash
npm i -g @openai/codex gpt-image-2-skill
codex login

mkdir -p ~/.local/share/nous-codex
curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs \
  -o ~/.local/share/nous-codex/nous-codex.mjs
cd ~/.local/share/nous-codex

node nous-codex.mjs pair ABCD2345          # optionally: --name "studio mac"
node nous-codex.mjs install-service        # run at login, restart on crash
node nous-codex.mjs status
```

`node nous-codex.mjs run` keeps it in the foreground instead — useful for a
first try, but it dies with the terminal, which is what `install-service` is
for.

(`npx @nous/codex-daemon` will work once the package is published to npm.)

## Commands

| Command | What it does |
|---|---|
| `pair <CODE> [--name <device>]` | Pair with your nous account. `--name` defaults to the machine hostname — set it when several machines would otherwise show up with the same name. |
| `run` | Stay connected in the foreground and take jobs. |
| `install-service` | Register a per-user service so the daemon starts at login/boot and restarts if it crashes. |
| `uninstall-service` | Stop and remove that service. |
| `status` | Print the config file, device id, why the daemon last stopped for good (if it did), whether the service is installed and running, and the CLI preflight report. |

## Platform support

| Platform | Mechanism | Verified |
|---|---|---|
| Linux | `systemd --user` unit at `~/.config/systemd/user/nous-codex.service`, plus `loginctl enable-linger` so it also runs when you are not logged in | ✅ on a real machine |
| macOS | LaunchAgent `~/Library/LaunchAgents/ink.nous.codex.plist`, logs at `~/Library/Logs/nous-codex.log` | ⚠️ not verified (no mac in the loop) |
| Windows | Scheduled task `NousCodex`, trigger ON LOGON | ⚠️ not verified |

`install-service` bakes the **PATH of the shell you ran it from** into the
unit/plist. Service managers start jobs with a minimal PATH that contains
neither `~/.local/bin` nor Homebrew, so without this the daemon comes up
reporting `codex` / `gpt-image-2-skill` / `dreamina` all missing and bounces
every job with `cli_missing` — observed on a real machine before the fix. If
you later move a CLI somewhere new, re-run `install-service` from a shell
where it resolves.

## Revocation is a dead end, on purpose

Revoke a device from the nous settings page and the server closes the socket
with code `4003` (`4001` if the token is otherwise invalid). The daemon
treats both as **final**: it prints

```
device revoked or token invalid — re-pair from nous Settings → Local CLI
```

records the reason, and **exits 0**. Every other close code (network blips,
server restarts, `1006`) is still retried with exponential backoff.

Exit **0** is deliberate, and it is what makes the three platforms agree.
systemd's `Restart=on-failure` and launchd's `KeepAlive`/`SuccessfulExit=false`
both mean "relaunch only after a *non-zero* exit", so a clean exit stops the
service for good everywhere — no `RestartPreventExitStatus`, no job that has
to unload itself. A crash still gets restarted, because a crash is non-zero.

The cost of exiting cleanly is that afterwards nothing looks unusual, so the
reason is written to `~/.config/nous-codex/last_stop.json`:

```json
{ "reason": "revoked", "at": "2026-08-26T12:28:17.101Z" }
```

`status` reads it back:

```
last stop   : this device was revoked in nous — re-pair to use it again (stopped 2026-08-26T12:28:17.101Z)
```

`reason` is `revoked` (the socket was closed under you, `4003`) or
`auth_failed` (the token no longer resolves, `4001` — this is what a device
that was *offline* when it got revoked sees on its next connect). A successful
`pair`, and any successful connect, deletes the file, so `status` never reports
a stop that no longer applies.

⚠️ **"Stopped" does not mean "uninstalled."** The unit is still enabled
(`WantedBy=default.target`, or `RunAtLoad` on macOS), so a revoked device
still starts once at every boot/login, fails to authenticate, prints the
reason and exits again. That is one short-lived process per boot, not a
reconnect loop — but if you want it gone, run:

```bash
node ~/.local/share/nous-codex/nous-codex.mjs uninstall-service
```

## Only run the command nous gave you

Install by copying the command out of **Settings → AI → Local CLI → Pair a
device** verbatim. It is a `curl … | sh` from this repository; a pairing code
that arrived any other way (chat, email, a page that is not nous) is worth
exactly as much trust as its sender. This program runs CLIs on your machine
with your logins.

## What it will and will not do

It runs **exactly three commands**, always with an argument array (never a
shell string), so nothing the server sends can be interpreted as shell
syntax:

- `gpt-image-2-skill images generate|edit …`
- `codex exec --json …`
- `dreamina <whitelisted subcommand> …` (all other subcommands refused)

Reference images are downloaded only from the nous API host; any other URL
is refused. Config lives in `~/.config/nous-codex/config.json` (mode 600)
and holds only the device token issued at pairing.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `NOUS_API_BASE` | `https://api.nous.ink` | Point at a different nous deployment. On Linux and macOS `install-service` bakes it into the unit/plist, so the background service uses the same one. **On Windows it is not** — a scheduled task carries no environment of its own, so set it as a user environment variable (`setx NOUS_API_BASE "…"`) before the task runs. |

## Development

```bash
cd tools/codex-daemon
npm test        # node --test — pure helpers: close-code triage, version
                # parsing, unit/plist rendering, pair argv
```
