-- Audio bitrate (kbps) probed on upload, and user-supplied lyrics for uploaded
-- audio. cover_image_path already exists (mig 044) and is reused for covers.
ALTER TABLE resources ADD COLUMN IF NOT EXISTS audio_bitrate_kbps integer;
ALTER TABLE resources ADD COLUMN IF NOT EXISTS lyrics_json jsonb;

COMMENT ON COLUMN resources.audio_bitrate_kbps IS 'Audio bitrate in kbps, ffprobed on upload for audio/* resources';
COMMENT ON COLUMN resources.lyrics_json IS 'User lyrics for uploaded audio: {lrc: text, lines: [{text, line_start_ms}]}';

NOTIFY pgrst, 'reload schema';
