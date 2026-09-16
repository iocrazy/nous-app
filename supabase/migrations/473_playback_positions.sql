-- 473: playback_positions — where each user got to in each video, shared
-- across their devices.
--
-- Until now this lived only in localStorage (migration-free, instant, works
-- offline), which is per browser profile: a video watched to 20 minutes on the
-- phone still started at 00:00 on the laptop. The local store stays — it is
-- what makes a deploy-triggered reload resumable with no network round trip —
-- and this table is the cross-device source of truth it reconciles against.
--
-- Shape notes:
--
-- * `media_key` is the SAME stable key the client already computes
--   (`resource:<id>`, `media:<platform_id>`, or a URL path fallback). It is
--   deliberately an opaque string rather than a FK to `resources`: positions
--   must also work for parsed media and for share-link paths that have no
--   resources row, and a dangling position is harmless — the worst case is a
--   row nobody ever reads again.
-- * One row per (user, media). The unique index is what makes the upsert an
--   upsert; without it every heartbeat would append.
-- * `updated_at` is SERVER time (`now()`), never client-supplied. It is the
--   arbiter the client uses to tell "another device wrote this" from "this is
--   my own write coming back", and two devices' wall clocks cannot be trusted
--   to order anything.

CREATE TABLE IF NOT EXISTS public.playback_positions (
    id                BIGINT PRIMARY KEY DEFAULT generate_snowflake_id(),
    user_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    media_key         TEXT NOT NULL,
    position_seconds  DOUBLE PRECISION NOT NULL,
    duration_seconds  DOUBLE PRECISION NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT playback_positions_position_nonneg CHECK (position_seconds >= 0),
    CONSTRAINT playback_positions_duration_positive CHECK (duration_seconds > 0),
    -- Guard against a client sending a key long enough to bloat the index.
    CONSTRAINT playback_positions_media_key_len CHECK (char_length(media_key) BETWEEN 1 AND 512)
);

-- Both the upsert target and the only read path (user + key).
CREATE UNIQUE INDEX IF NOT EXISTS uq_playback_positions_user_media
    ON public.playback_positions (user_id, media_key);

-- "Continue watching" reads the newest first for one user.
CREATE INDEX IF NOT EXISTS idx_playback_positions_user_updated
    ON public.playback_positions (user_id, updated_at DESC);

COMMENT ON TABLE public.playback_positions IS
    'Per-user playback position per media, synced across devices. Mirrors the '
    'browser-local store in frontend/utils/playbackResume.ts; updated_at is '
    'server time and is the arbiter for which device wrote last.';
COMMENT ON COLUMN public.playback_positions.media_key IS
    'Opaque stable media identity as computed by the client '
    '(resource:<id> / media:<platform_id> / URL path). Not a FK on purpose — '
    'positions exist for media with no resources row.';

-- `updated_at` moves on every UPDATE, enforced by the table rather than by the
-- one code path that writes it. That timestamp is the arbiter the client uses
-- to tell "my own write came back" from "another device wrote after me", so a
-- writer that forgot to touch it would break sync silently — exactly the class
-- of bug a trigger removes. Reuses the schema's shared function.
DROP TRIGGER IF EXISTS playback_positions_touch_updated_at ON public.playback_positions;
CREATE TRIGGER playback_positions_touch_updated_at
    BEFORE UPDATE ON public.playback_positions
    FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- RLS: this table is reached only through the backend (direct SQL /
-- service_role), never from the browser via PostgREST. Enable RLS with no
-- permissive policy so an anon/authenticated key cannot read other users'
-- viewing history even if someone points PostgREST at it later.
ALTER TABLE public.playback_positions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.playback_positions FROM PUBLIC, anon, authenticated;
