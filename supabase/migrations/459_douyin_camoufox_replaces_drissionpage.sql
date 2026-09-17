-- Douyin browser tier: DrissionPage → Camoufox (2026-09-16)
--
-- The runtime changed engines. DrissionPage drove Chrome over CDP, which
-- douyin detects through the automation protocol itself rather than the
-- fingerprint surface, so no amount of stealth patching on top could answer
-- it. Camoufox is Firefox over Playwright's own protocol.
--
-- This migration carries the operator's EXISTING choice across the rename.
-- Defaulting the new key to 'true' regardless would silently re-enable a
-- tier an admin had deliberately switched off, which is the kind of change
-- nobody notices until a browser starts up in production.
--
-- Migration 109 created `douyin_drissionpage_enabled`; 125 added
-- `douyin_abogus_enabled`. Neither is edited here — history stays as it was
-- applied.

-- 1) Carry the old toggle's value onto the new key.
INSERT INTO system_settings (key, value, description)
SELECT
  'douyin_camoufox_enabled',
  COALESCE(
    (SELECT value FROM system_settings WHERE key = 'douyin_drissionpage_enabled'),
    'true'::jsonb
  ),
  'Enable Camoufox browser automation for Douyin (slowest, most reliable)'
ON CONFLICT (key) DO NOTHING;

-- 2) Retire the dead key. Leaving it would show admins a toggle that no
--    longer controls anything — parse_chain stopped reading it.
DELETE FROM system_settings WHERE key = 'douyin_drissionpage_enabled';

-- 3) Per-user parse_mode pins that named the old engine.
--    jsonb_set on a key that is absent is a no-op, so the WHERE clause is
--    what keeps this from touching users who never pinned a mode.
UPDATE user_settings
SET settings_json = jsonb_set(
      settings_json,
      '{parse_mode}',
      '"camoufox"'::jsonb,
      false
    )
WHERE settings_json ->> 'parse_mode' = 'drissionpage';
