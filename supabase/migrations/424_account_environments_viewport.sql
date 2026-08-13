-- Migration 424: account_environments — per-account viewport + backfill the
-- rows that mig 402 created a table for and nobody ever wrote (P2-4).
--
-- Ledger: docs/superpowers/specs/2026-08-09-distribution-gap-closure-plan.md (P2-4)
-- Table:  supabase/migrations/402_account_environments.sql (spec §3.2)
--
-- Two things, landed together because the second is what makes the first
-- observable.
--
-- (a) viewport_width / viewport_height
--     ------------------------------------------------------------------
--     The table shipped with the axes Playwright exposes MINUS the only one
--     that can actually differ per account today. Measured on the live
--     nous-browser container (Chromium 145.0.7632.6, headed under Xvfb) on
--     2026-08-12:
--
--       * user_agent  — Playwright's `user_agent` context option rewrites
--         navigator.userAgent and the User-Agent header, and NOTHING ELSE.
--         Overriding it to Chrome/999.0.0.0 left
--         `Sec-CH-UA: "Chromium";v="145"` and
--         `navigator.userAgentData.uaFullVersion = "145.0.7632.6"` untouched.
--         So a stored UA string is a self-contradiction the moment the browser
--         image bumps Chromium — and modern Chrome's *reduced* UA
--         (`Chrome/145.0.0.0`, minor/build zeroed by design) carries no
--         per-account entropy to begin with. The only always-consistent UA is
--         the browser's own, i.e. NULL. See the column comment.
--       * locale / timezone_id — must be zh-CN / Asia/Shanghai for a Chinese
--         platform reached over a Chinese residential IP. Varying them is the
--         alarm, not the camouflage (mig 402's own header says as much).
--       * geo_lat / geo_lng — setting them makes the browser side also grant
--         `permissions: ["geolocation"]`, so the page gets coordinates with no
--         prompt. A real fresh profile never does. Stays NULL.
--       * proxy_url — no proxy pool exists yet. Column and its decrypt path
--         stay live for when one does.
--
--     That leaves window size: the one axis where every value is plausible,
--     nothing else can contradict it, and variation between runs is *expected*
--     in the wild (people resize windows). Today every context takes
--     Playwright's default 1280x720, so three accounts are byte-identical here.
--
--     The CHECK floor is 1280x720 on purpose — it is the size the Douyin DOM
--     automation has always run at, so a pinned viewport can only ever give a
--     page MORE room than the flow is known to work in, never less. That rules
--     out "a narrower viewport flipped the creator centre into a compact layout
--     and broke the selectors" as a failure mode. The ceiling is the Xvfb
--     screen (XVFB_SCREEN=1920x1080), which a headed window has to fit inside.
--
-- (b) Backfill for the accounts that already exist
--     ------------------------------------------------------------------
--     MioPoo / HEYGO / iocrazy have published from the implicit defaults since
--     they were bound. Handing them a NEW fingerprint would itself be a change
--     signal on three accounts at once — the exact correlated event this
--     feature exists to avoid. So the backfill writes their CURRENT EFFECTIVE
--     values and nothing else: locale/timezone spelled out (identical to
--     browser_client.DEFAULT_LOCALE / DEFAULT_TIMEZONE_ID, which is what
--     `build_environment(None)` already produces), viewport NULL (= keep
--     Playwright's default). Zero observable change; the row now exists so a
--     proxy or a viewport can be assigned deliberately later.
--
--     `auth_type = 'session'` only: OAuth accounts never open a browser.
--     Soft-deleted rows included — a re-bind wakes the row (mig 416) and
--     should wake it with its own environment, not a fresh one.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS, DROP CONSTRAINT IF EXISTS before ADD,
-- INSERT ... ON CONFLICT DO NOTHING. Re-apply is a no-op — and specifically it
-- will NOT overwrite an environment that has since been assigned a proxy or a
-- viewport, which is the property the whole feature rests on.

BEGIN;

ALTER TABLE public.account_environments
  ADD COLUMN IF NOT EXISTS viewport_width INTEGER,
  ADD COLUMN IF NOT EXISTS viewport_height INTEGER;

-- Both or neither: a half-set viewport cannot be handed to new_context(), so
-- the browser side would silently ignore it — a column that looks configured
-- and does nothing is the failure mode this constraint exists to prevent.
--
-- ⚠️ The IS NOT NULL pair is load-bearing, not belt-and-braces. Without it the
-- second branch reads `1440 BETWEEN 1280 AND 1920 AND NULL BETWEEN 720 AND 1080`
-- = `true AND NULL` = NULL, and a CHECK treats NULL as SATISFIED — so a
-- width-only row sails straight through the constraint that exists to stop it.
-- Verified against the live DB on 2026-08-12: the first draft of this CHECK
-- accepted `SET viewport_width=1440` with the height left NULL.
ALTER TABLE public.account_environments
  DROP CONSTRAINT IF EXISTS account_environments_viewport_check;
ALTER TABLE public.account_environments
  ADD CONSTRAINT account_environments_viewport_check
  CHECK (
    (viewport_width IS NULL AND viewport_height IS NULL)
    OR (
      viewport_width IS NOT NULL
      AND viewport_height IS NOT NULL
      AND viewport_width BETWEEN 1280 AND 1920
      AND viewport_height BETWEEN 720 AND 1080
    )
  );

COMMENT ON COLUMN public.account_environments.viewport_width IS
  'Playwright new_context(viewport=...) width. NULL = library default (1280). Floor 1280 = the size the DOM automation is known to work at.';
COMMENT ON COLUMN public.account_environments.viewport_height IS
  'Playwright new_context(viewport=...) height. NULL = library default (720). Ceiling 1080 = the Xvfb screen a headed window must fit inside.';
COMMENT ON COLUMN public.account_environments.user_agent IS
  'Playwright new_context(user_agent=...). Kept NULL deliberately: the option does not update Client Hints (Sec-CH-UA / navigator.userAgentData still report the real Chromium build), so any stored string contradicts them as soon as the browser image moves. NULL = the browser''s own UA, the only one guaranteed self-consistent.';

-- (b) backfill — see header. Current effective values only.
INSERT INTO public.account_environments (account_id, locale, timezone_id)
SELECT id, 'zh-CN', 'Asia/Shanghai'
  FROM public.social_accounts
 WHERE auth_type = 'session'
ON CONFLICT (account_id) DO NOTHING;

NOTIFY pgrst, 'reload schema';

COMMIT;
