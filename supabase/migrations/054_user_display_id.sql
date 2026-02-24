-- 054_user_display_id.sql
-- Add Snowflake display_id to user_profiles for URL-friendly user identification.
-- auth.users.id (UUID) remains the internal FK; display_id is for external use.

-- ============================================================================
-- Part 1: Add display_id column
-- ============================================================================

ALTER TABLE user_profiles
ADD COLUMN IF NOT EXISTS display_id BIGINT UNIQUE DEFAULT generate_snowflake_id();

-- ============================================================================
-- Part 2: Backfill existing rows
-- ============================================================================

UPDATE user_profiles
SET display_id = generate_snowflake_id()
WHERE display_id IS NULL;

-- ============================================================================
-- Part 3: Add NOT NULL constraint after backfill
-- ============================================================================

ALTER TABLE user_profiles
ALTER COLUMN display_id SET NOT NULL;
