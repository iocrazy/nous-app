-- 122_ai_session_counter_rpc.sql
-- Atomic counter increment for AI sessions (fix TOCTOU in _save_messages).
--
-- Before: application did SELECT total_tokens / message_count, then UPDATE with
-- current + delta.  Two concurrent chat calls could read the same baseline and
-- each overwrite with (baseline + 2), losing one increment.
--
-- After: single-statement UPDATE performed by Postgres, safe under any
-- concurrency level.

CREATE OR REPLACE FUNCTION increment_ai_session_counters(
    p_session_id UUID,
    p_tokens INT,
    p_messages INT DEFAULT 2
) RETURNS void
LANGUAGE sql
AS $$
    UPDATE ai_sessions
    SET
        total_tokens = COALESCE(total_tokens, 0) + p_tokens,
        message_count = COALESCE(message_count, 0) + p_messages,
        updated_at = now()
    WHERE id = p_session_id;
$$;

-- Allow authenticated and service_role clients to invoke the function.
GRANT EXECUTE ON FUNCTION increment_ai_session_counters(UUID, INT, INT) TO authenticated, service_role;
