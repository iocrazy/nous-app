-- 079_resource_statistics_view.sql
-- Create resource_statistics view to replace dropped media_statistics.

CREATE OR REPLACE VIEW resource_statistics AS
SELECT
  r.creator_id AS user_id,
  count(*) AS total_resources,
  count(*) FILTER (WHERE r.file_type = 'video') AS video_count,
  count(*) FILTER (WHERE r.file_type = 'image') AS image_count,
  count(*) FILTER (WHERE r.file_type = 'audio') AS audio_count,
  count(*) FILTER (WHERE r.file_type = 'document') AS document_count,
  coalesce(sum(r.file_size_bytes), 0) AS total_size_bytes,
  count(*) FILTER (WHERE r.is_trashed = true) AS trashed_count
FROM resources r
GROUP BY r.creator_id;
