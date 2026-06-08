-- 273_resource_chorus_start.sql
-- User-settable chorus (highlight) start point for uploaded audio, in ms.
-- Mirrors the unit of parsed_media.metadata.chorus.start (ms) so the frontend
-- divides by 1000 and reuses the existing AudioWaveformPlayer amber marker.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS chorus_start_ms integer;

COMMENT ON COLUMN resources.chorus_start_ms IS 'Chorus/highlight start in ms for uploaded audio; NULL = unset';

NOTIFY pgrst, 'reload schema';
