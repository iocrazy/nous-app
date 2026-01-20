-- Add color and is_active fields to smart_collections table
ALTER TABLE smart_collections
ADD COLUMN IF NOT EXISTS color VARCHAR(20) DEFAULT NULL,
ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

-- Add comment for clarity
COMMENT ON COLUMN smart_collections.color IS 'Optional color for collection display (e.g., #6366f1)';
COMMENT ON COLUMN smart_collections.is_active IS 'Whether the collection is currently active';
