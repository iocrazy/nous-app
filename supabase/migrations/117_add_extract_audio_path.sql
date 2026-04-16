-- Add extract_audio_path to parsed_media
-- Stores the path of audio extracted FROM video for AI transcription.
-- Distinct from music_download_path which stores original platform BGM/music.
ALTER TABLE parsed_media ADD COLUMN IF NOT EXISTS extract_audio_path TEXT;
