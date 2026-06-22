-- 306_hotspot_user_state.sql
-- Per-user state for global hotspots: read / saved / hidden.
-- hotspots are global rows (user_id NULL, one fetch shared by all). Personal
-- state therefore lives in its own table keyed by (user_id, hotspot_id).

CREATE TABLE IF NOT EXISTS hotspot_user_state (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    hotspot_id BIGINT NOT NULL REFERENCES hotspots(id) ON DELETE CASCADE,
    is_read BOOLEAN NOT NULL DEFAULT false,
    is_saved BOOLEAN NOT NULL DEFAULT false,
    is_hidden BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, hotspot_id)
);

-- Fast lookups for the "Saved" / "Hidden" filter views.
CREATE INDEX IF NOT EXISTS idx_hotspot_user_state_saved
    ON hotspot_user_state (user_id) WHERE is_saved;
CREATE INDEX IF NOT EXISTS idx_hotspot_user_state_hidden
    ON hotspot_user_state (user_id) WHERE is_hidden;

ALTER TABLE hotspot_user_state ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Manage own hotspot state" ON hotspot_user_state FOR ALL
    USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Service role full access on hotspot_user_state" ON hotspot_user_state
    FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- PostgREST: reload schema cache so the new table/columns are visible.
NOTIFY pgrst, 'reload schema';
