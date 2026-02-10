-- 039: Add key_value column to api_keys for persistent full key access
-- Previously, full key was only shown once after creation (hash-only storage).
-- Now the full key is stored and always accessible.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS key_value VARCHAR;
