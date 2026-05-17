# Supabase MCP setup (Claude Code)

Copy `.mcp.json.example` to `.mcp.json` (gitignored), then fill in the key.

## 1. SSH tunnel to the NAS Supabase Kong gateway

LAN IPs are blocked for user-installed binaries on macOS 26+ (Local Network privacy enforcement). Loopback is exempt, so we tunnel.

```bash
# dev stack (default — see .mcp.json.example)
ssh -fN -L 9081:127.0.0.1:9081 192.168.50.9

# prod stack (only when you really need prod)
ssh -fN -L 9082:127.0.0.1:9082 192.168.50.9

# verify
lsof -nP -iTCP:9081 -sTCP:LISTEN
```

Persistent tunnels: use Termius / autossh / a launchd plist so it survives reboot.

## 2. Get the MCP API key from the Kong container

The key is env-var-managed (not committed). Each stack has its own:

```bash
# dev
ssh 192.168.50.9 'sudo docker exec mediahub-sb-dev-kong env | grep MCP_API_KEY'
# prod
ssh 192.168.50.9 'sudo docker exec mediahub-sb-prod-kong env | grep MCP_API_KEY'
```

Paste the value into the `x-api-key` header in your `.mcp.json`.

## 3. Verify

```bash
claude mcp list | grep supabase
# expect: supabase: http://127.0.0.1:9081/mcp (HTTP) - ✓ Connected
```

If `✗ Failed to connect`: check tunnel is up, key matches Kong env, and that the `.mcp.json` URL uses `127.0.0.1` not `192.168.50.9`.

## Switching between dev and prod

Just change the URL port (`9081` ↔ `9082`) and the key. Both stacks share the same MCP API contract.

## Background

- Kong route patch (POST → studio, GET → SSE stub) and macOS Local Network details: see Claude `memory/reference_nas_supabase_stacks.md` (local, not in repo).
- The two Kong stacks live on the NAS at `/volume1/docker/datahub/mediahub-sb-{dev,prod}/`.
