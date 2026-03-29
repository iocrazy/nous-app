-- Add system settings for Douyin parse method toggles
-- Each can be independently enabled/disabled in Admin panel

INSERT INTO system_settings (key, value, description)
VALUES
  ('douyin_ytdlp_enabled', 'true', 'Enable yt-dlp for Douyin parsing (requires cookie)'),
  ('douyin_lighthttp_enabled', 'true', 'Enable LightHTTP parser for Douyin (fast, no browser)'),
  ('douyin_drissionpage_enabled', 'true', 'Enable DrissionPage browser automation for Douyin (slowest, most reliable)')
ON CONFLICT (key) DO NOTHING;
