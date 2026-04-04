-- Add team_id to shares for team-based filtering
ALTER TABLE shares ADD COLUMN IF NOT EXISTS team_id bigint REFERENCES teams(id);

-- Create index for team filtering
CREATE INDEX IF NOT EXISTS idx_shares_team_id ON shares(team_id);

-- Backfill: derive team_id from project_file_id → project_files → projects
UPDATE shares s
SET team_id = p.team_id
FROM project_files pf
JOIN projects p ON pf.project_id = p.id
WHERE s.project_file_id = pf.id
  AND s.team_id IS NULL
  AND p.team_id IS NOT NULL;
