-- Enable Supabase Realtime on application_logs for Live Tail feature
ALTER PUBLICATION supabase_realtime ADD TABLE application_logs;
