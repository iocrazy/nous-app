-- Migration 096: Backfill file_size_bytes on resources and resource_versions
-- from parsed_media.storage_size for existing records where file_size_bytes is missing

-- Step 1: Backfill parsed_media.datasize_bytes from storage_size where it's 0 but storage_size exists
UPDATE parsed_media
SET datasize_bytes = storage_size
WHERE (datasize_bytes IS NULL OR datasize_bytes = 0)
  AND storage_size IS NOT NULL
  AND storage_size > 0;

-- Step 2: Backfill resources.file_size_bytes from parsed_media.storage_size (or datasize_bytes)
UPDATE resources r
SET file_size_bytes = COALESCE(pm.storage_size, pm.datasize_bytes)
FROM parsed_media pm
WHERE r.media_id = pm.id
  AND (r.file_size_bytes IS NULL OR r.file_size_bytes = 0)
  AND COALESCE(pm.storage_size, pm.datasize_bytes) > 0;

-- Step 3: Backfill resource_versions.file_size_bytes from resources.file_size_bytes
UPDATE resource_versions rv
SET file_size_bytes = r.file_size_bytes
FROM resources r
WHERE rv.resource_id = r.id
  AND (rv.file_size_bytes IS NULL OR rv.file_size_bytes = 0)
  AND r.file_size_bytes IS NOT NULL
  AND r.file_size_bytes > 0;
