-- 185_boundary_audit.sql
-- Boundary layer (B9-G) — audit table for SSRF / boundary policy blocks.
--
-- Every block from validate_url, SafeAsyncClient redirect hook, or
-- SsrfProxy lands a row here. Operators query this table to see
-- attack attempts, false positives, and trends. Server-side only —
-- the raw URL must NOT leak back to the client (the API layer returns
-- a generic safe message).

CREATE TABLE IF NOT EXISTS boundary_audit (
    id BIGSERIAL PRIMARY KEY,
    blocked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    layer TEXT NOT NULL,
        -- 'l1_validate'  | url_guard.validate_url[_async]
        -- 'l2_pinned'    | PinnedDNSResolver
        -- 'l3_safehttp'  | SafeAsyncClient (in-process)
        -- 'l3_proxy'     | SsrfProxy (subprocess + browser)
    reason TEXT NOT NULL,
        -- short canonical reason: 'private_ip' | 'blocked_scheme'
        -- | 'redirect_blocked' | 'mixed_dns_private' | 'invalid_target'
        -- | 'metadata_host' | 'suffix_blocked' | etc.
    raw_url TEXT,
        -- The rejected URL or target. Server-side ONLY — never returned
        -- to the client. Truncated to 2000 chars.
    resolved_ip TEXT,
        -- If DNS was attempted, the IP that triggered the block.
    user_id BIGINT,
        -- If available from request context (best-effort).
    request_id TEXT,
        -- For correlating with application_logs / api_request_logs.
    metadata_json JSONB DEFAULT '{}'::jsonb
        -- Free-form extra: header origin, redirect chain depth, etc.
);

-- Most queries: "show me recent blocks", "show blocks for user X".
CREATE INDEX IF NOT EXISTS boundary_audit_blocked_at_idx
    ON boundary_audit (blocked_at DESC);
CREATE INDEX IF NOT EXISTS boundary_audit_user_id_idx
    ON boundary_audit (user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS boundary_audit_layer_reason_idx
    ON boundary_audit (layer, reason);

COMMENT ON TABLE boundary_audit IS
    'Boundary layer block audit log. Server-side only; raw_url never echoed to client. Sweeper auto-prunes >90d (B9-G follow-up).';
