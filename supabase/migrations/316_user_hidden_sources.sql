-- 316_user_hidden_sources.sql
-- Per-user "close/hide" of a signal source.
--
-- A system source (signal_sources.user_id IS NULL) keeps collecting for
-- everyone; a client who doesn't want to see it in their own feed records a
-- row here. The source is untouched — other clients still see it. This is the
-- per-client view customization, distinct from disabling/deleting a source
-- (which stops collection globally and is only allowed on one's OWN sources).

CREATE TABLE IF NOT EXISTS user_hidden_sources (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES signal_sources(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, source_id)
);

-- Feed assembly fetches "which sources has this user hidden" on every list call.
CREATE INDEX IF NOT EXISTS idx_user_hidden_sources_user
    ON user_hidden_sources (user_id);

ALTER TABLE user_hidden_sources ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Manage own hidden sources" ON user_hidden_sources FOR ALL
    USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Service role full access on user_hidden_sources" ON user_hidden_sources
    FOR ALL USING (auth.jwt()->>'role' = 'service_role');

-- PostgREST: reload schema cache so the new table is visible.
NOTIFY pgrst, 'reload schema';
