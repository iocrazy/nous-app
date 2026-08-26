# nous-codex

Run nous canvas generations **on your own machine, with your own codex login**.

Your codex credentials never leave your computer. nous only sends job
descriptions over an outbound connection; this program runs the CLI locally
and uploads the finished file back.

## Requirements

- Node.js 20+
- A ChatGPT subscription (the whole point: generations spend YOUR quota)

```bash
npm i -g @openai/codex gpt-image-2-skill
codex login        # browser OAuth; credentials stay in ~/.codex/auth.json
```

`run` checks both CLIs and the login at startup and prints exactly what is
missing, so you never discover a gap through a failed job.

## Install & pair

1. In nous: **Settings → Local codex → Pair a device** → copy the 8-character code.
2. On your machine:

```bash
curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs -o nous-codex.mjs
node nous-codex.mjs pair ABCD2345
node nous-codex.mjs run
```

(`npx @nous/codex-daemon` will work once the package is published to npm.)

`run` stays connected and takes jobs. Keep it running (or add it to your
login items / a systemd user unit).

## What it will and will not do

It runs **exactly two commands**, always with an argument array (never a
shell string), so nothing the server sends can be interpreted as shell
syntax:

- `gpt-image-2-skill images generate|edit …`
- `codex exec --json …`

Reference images are downloaded only from the nous API host; any other URL
is refused. Config lives in `~/.config/nous-codex/config.json` (mode 600)
and holds only the device token issued at pairing — revoke it any time from
the nous settings page and this device stops working immediately.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `NOUS_API_BASE` | `https://api.nous.ink` | Point at a different nous deployment |
