-- Add system setting for the ABogus parse method (a_bogus signed HTTP).
-- Inserts as fourth tier in the Douyin fallback chain:
-- yt-dlp → LightHTTP → ABogus → DrissionPage

INSERT INTO system_settings (key, value, description)
VALUES
  ('douyin_abogus_enabled', 'true', 'Enable a_bogus signed HTTP parser for Douyin (requires Node.js in backend container)')
ON CONFLICT (key) DO NOTHING;
