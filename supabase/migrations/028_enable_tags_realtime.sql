-- Enable realtime for tags and video_tags tables
ALTER PUBLICATION supabase_realtime ADD TABLE tags;
ALTER PUBLICATION supabase_realtime ADD TABLE video_tags;
