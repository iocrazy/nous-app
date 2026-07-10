-- 354: support project/episode renders listing (generated_media.node_id join to shots)
CREATE INDEX IF NOT EXISTS idx_genmedia_node_shot
  ON generated_media(node_id)
  WHERE origin_kind IN ('shot_generate','shot_video');
