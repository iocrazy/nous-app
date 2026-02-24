-- 056_backfill_resources.sql
-- Backfill resource + resource_items records for videos that were downloaded
-- via Celery but don't have corresponding resource records.
-- This bridges the gap between the videos table and the Resource Library.

DO $$
DECLARE
  v RECORD;
  new_resource_id BIGINT;
BEGIN
  FOR v IN
    SELECT
      vid.id,
      vid.user_id,
      vid.title,
      vid.download_path,
      vid.datasize_bytes,
      vid.duration,
      vid.resolution,
      vid.cover_download_path,
      vid.media_type
    FROM videos vid
    WHERE vid.video_download_status = 'completed'
      AND vid.download_path IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM resources r WHERE r.video_id = vid.id
      )
  LOOP
    -- Determine file_type based on media_type
    -- media_type 0/4/61 = video, 2/68 = image set
    DECLARE
      v_file_type VARCHAR(50);
      v_mime_type VARCHAR(100);
    BEGIN
      IF v.media_type IN ('2', '68') THEN
        v_file_type := 'image';
        v_mime_type := 'image/jpeg';
      ELSE
        v_file_type := 'video';
        v_mime_type := 'video/mp4';
      END IF;

      -- Create resource record
      INSERT INTO resources (
        creator_id, source_type, video_id, filename, file_type,
        mime_type, file_path, file_size_bytes, cover_image_path,
        duration_seconds, resolution
      )
      VALUES (
        v.user_id,
        'web',
        v.id,
        COALESCE(v.title, 'Untitled'),
        v_file_type,
        v_mime_type,
        v.download_path,
        v.datasize_bytes,
        v.cover_download_path,
        -- Parse duration string (format: "MM:SS" or "HH:MM:SS") to seconds
        CASE
          WHEN v.duration IS NULL THEN NULL
          WHEN v.duration ~ '^\d+:\d+:\d+$' THEN
            EXTRACT(EPOCH FROM v.duration::interval)::INTEGER
          WHEN v.duration ~ '^\d+:\d+$' THEN
            EXTRACT(EPOCH FROM ('00:' || v.duration)::interval)::INTEGER
          WHEN v.duration ~ '^\d+$' THEN
            v.duration::INTEGER
          ELSE NULL
        END,
        v.resolution
      )
      RETURNING id INTO new_resource_id;

      -- Create resource_item (personal scope)
      INSERT INTO resource_items (resource_id, scope_type, scope_id, added_by)
      VALUES (new_resource_id, 'personal', v.user_id, v.user_id);
    END;
  END LOOP;

  RAISE NOTICE 'Backfill complete';
END $$;
