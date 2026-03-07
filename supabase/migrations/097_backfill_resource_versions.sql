-- Backfill missing resource_versions for downloaded videos.
-- Some resources created via the download flow never got a v1 resource_version record.
-- This migration creates v1 entries for all resources that have a file_path but no version.

INSERT INTO resource_versions (resource_id, version_number, filename, file_path, file_size_bytes, mime_type, uploaded_by)
SELECT
    r.id AS resource_id,
    1 AS version_number,
    -- Extract filename from file_path
    CASE
        WHEN r.file_path LIKE '%/%' THEN substring(r.file_path FROM '[^/]+$')
        ELSE r.file_path
    END AS filename,
    r.file_path,
    r.file_size_bytes,
    COALESCE(
        CASE
            WHEN r.file_path LIKE '%.webm' THEN 'video/webm'
            WHEN r.file_path LIKE '%.mkv' THEN 'video/x-matroska'
            WHEN r.file_path LIKE '%.mp4' THEN 'video/mp4'
            ELSE 'video/mp4'
        END,
        'video/mp4'
    ) AS mime_type,
    r.creator_id AS uploaded_by
FROM resources r
LEFT JOIN resource_versions rv ON rv.resource_id = r.id
WHERE rv.id IS NULL
  AND r.file_path IS NOT NULL;
