-- 256_fix_get_user_tag_counts.sql
--
-- Repair the get_user_tag_counts RPC after two schema migrations broke it:
--   * migration 077 dropped media_tags (merged into resource_tags)
--   * migration 083 dropped parsed_media.user_id (per-user ownership moved to
--     resources.creator_id)
--   * migration 051 migrated tags.id from UUID to BIGINT snowflake
--
-- The mig 066 body JOINed media_tags + parsed_media.user_id, so every call
-- raised `relation "media_tags" does not exist`, which made the tag-statistics
-- endpoint always fall through to its (separately fixed) fallback.
--
-- Resource-centric redefinition: count per resource owner (resources.creator_id)
-- via resource_tags. Signature kept identical to the live function
-- (p_user_id uuid, p_limit integer); return columns kept identical EXCEPT id,
-- which is now BIGINT to match the snowflake tags.id column (was UUID in 066,
-- pre-051). search_path pinned per migration 127. SECURITY DEFINER / plpgsql
-- preserved from 066.

-- Postgres forbids changing a function's RETURNS TABLE column type via CREATE OR
-- REPLACE (the live mig 066 declared id UUID; we widen it to BIGINT to match the
-- snowflake tags.id). DROP first — as migrations 035 / 059 did for this same
-- function — using the exact existing arg signature (uuid, integer). Without this
-- DROP, CI's "Run SQL Migration on NAS" errors with "cannot change return type of
-- existing function" and the deploy is blocked.
DROP FUNCTION IF EXISTS get_user_tag_counts(uuid, integer) CASCADE;

CREATE OR REPLACE FUNCTION get_user_tag_counts(p_user_id UUID, p_limit INT DEFAULT 10)
RETURNS TABLE (
    id BIGINT,
    name VARCHAR,
    color VARCHAR,
    icon VARCHAR,
    type VARCHAR,
    count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        t.id,
        t.name,
        t.color,
        t.icon,
        t.type,
        COUNT(rt.resource_id) AS count
    FROM tags t
    JOIN resource_tags rt ON t.id = rt.tag_id
    JOIN resources r ON r.id = rt.resource_id
    WHERE r.creator_id = p_user_id
    GROUP BY t.id, t.name, t.color, t.icon, t.type
    ORDER BY count DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
   SET search_path = public, pg_catalog;

-- PostgREST caches function signatures; force a schema reload so RPC callers
-- pick up the new return shape (id BIGINT).
NOTIFY pgrst, 'reload schema';
