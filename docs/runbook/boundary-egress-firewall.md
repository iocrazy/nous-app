# Boundary Layer 4 — Egress Firewall Runbook

**Purpose**: kernel-level outbound deny rules so that even if Layer 1-3
(validate_url + SafeAsyncClient + SsrfProxy) are bypassed by a future
bug, the packet still cannot leave the container to reach internal
infrastructure.

**Audience**: NAS operator (heygo) deploying mediahub on Synology DSM 7.x
with Docker. Adapt to other deploy targets (Linux iptables, K8s
NetworkPolicy, Cloudflare WARP) as needed.

This runbook is the load-bearing document for the **Layer 4** row in the
Boundary architecture (see `docs/architecture/boundary-layer.md`).

---

## What this protects

The threat model: an attacker submits a URL → boundary code accepts it
(bug, novel bypass, or an unreviewed code path) → mediahub's container
makes an outbound TCP connection → connection lands on `192.168.50.9:9080`
(Supabase admin) or `192.168.50.X` (other NAS services) → admin panel
exposed.

Layers 1-3 should catch this. Layer 4 is the "kernel says no" backstop:
even if every Python check is wrong, the packet doesn't reach the
destination because the firewall drops it at the host.

## Recommended rules — Synology DSM Firewall (GUI)

DSM 7.x → Control Panel → Security → Firewall → Edit Rules
(profile: "default" or per-network-interface)

Add these OUTBOUND deny rules ABOVE the default allow rule. Apply to
the Docker bridge interface (typically `docker0` or `br-mediahub`).

| # | Action | Source | Destination | Port | Protocol | Note |
|---|--------|--------|-------------|------|----------|------|
| 1 | Allow | Docker subnet | 192.168.50.9 | 9080 | TCP | Supabase (intentional) |
| 2 | Allow | Docker subnet | 192.168.50.9 | 9081 | TCP | Supabase (intentional) |
| 3 | Allow | Docker subnet | 192.168.50.9 | 6379 | TCP | Redis (intentional) |
| 4 | Deny | Docker subnet | 192.168.50.0/24 | All | TCP/UDP | Block all other NAS LAN |
| 5 | Deny | Docker subnet | 10.0.0.0/8 | All | TCP/UDP | RFC1918 |
| 6 | Deny | Docker subnet | 172.16.0.0/12 | All | TCP/UDP | RFC1918 |
| 7 | Deny | Docker subnet | 169.254.169.254 | All | TCP/UDP | Cloud IMDS |
| 8 | Deny | Docker subnet | 127.0.0.0/8 | All | TCP/UDP | Loopback (host-mode containers) |
| 9 | Allow | Docker subnet | 0.0.0.0/0 | All | TCP/UDP | Default — public internet |

**Order matters**: DSM evaluates top-down, first match wins. Put the
intentional ALLOWs (rules 1-3) above the broad DENYs.

## Alternative — Docker Compose network isolation

Less surgical but cross-platform. Edit `docker/docker-compose.yml`:

```yaml
networks:
  mediahub_internal:
    driver: bridge
    internal: false  # need outbound public internet
    ipam:
      config:
        - subnet: 172.28.0.0/24

services:
  backend:
    networks:
      - mediahub_internal
    # Add a sidecar firewall (e.g. ufw, nftables) that runs as
    # privileged and applies the rules above to the container's iface.
```

This approach is harder to verify than DSM's built-in firewall. Prefer
the DSM rules unless deploying off-Synology.

## Alternative — Cloudflare WARP / Zero Trust

If using Cloudflare for ingress, also enable WARP egress for the
mediahub container. Cloudflare can enforce destination policies
(block private IPs at the network edge) without local firewall config.
Trade-off: adds latency, depends on a third-party.

## Verification

After applying rules, verify from inside the container:

```bash
# Should succeed (intentional allow):
docker compose exec backend curl -sf http://192.168.50.9:9080/health

# Should hang/fail with timeout (firewall drops the SYN):
docker compose exec backend curl --max-time 3 http://192.168.50.9:9090/anything
docker compose exec backend curl --max-time 3 http://10.0.0.1/
docker compose exec backend curl --max-time 3 http://169.254.169.254/

# Should succeed (public internet):
docker compose exec backend curl -sf https://www.google.com/
```

If the "should hang/fail" tests instead succeed, the rule order is
wrong (likely a broad ALLOW above the DENY). Re-check rule positions.

## Audit signal

After Layer 4 is active, watch `boundary_audit` (Layer 5) for blocks
with `reason = 'connect_blocked'` or `'http_blocked'` from `l3_proxy`.
A surge means either a real attack attempt or a Layer 1-3 false
positive that the firewall is correctly catching.

If `boundary_audit` is empty for >30 days, that does NOT mean the
firewall is unused — it means Layers 1-3 are catching everything before
it reaches the kernel. Both are working correctly.

## Failure modes

- **Container can't talk to Supabase**: rule 1/2 missing or below the
  rule-4 broad deny. Check rule order.
- **All public internet broken**: rule 9 (default allow) was deleted
  or is below rule 8 (loopback deny). Restore rule 9 to the bottom.
- **Some legitimate user URLs broken**: a public CDN may resolve to
  an unfamiliar IP range you didn't consider trusted. Check the URL's
  resolved IP against rule 4-8 ranges; widen the allow if needed.

## Related

- `docs/architecture/boundary-layer.md` — full boundary architecture
- `app/boundary/audit.py` — Layer 5 observability
- `supabase/migrations/185_boundary_audit.sql` — audit table schema
