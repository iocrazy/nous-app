-- Create the resource_analysis table (visual-analysis results + embeddings).
--
-- The original chain that produced this table — 014 (video_analysis) → 035/059
-- (video_id type churn) → 066 (→ media_analysis) → 076 (→ resource_analysis,
-- composite PK + FK to resources) — was NEVER applied to the current self-hosted
-- prod instance (the table, and the whole video_analysis/media_analysis lineage,
-- is absent — verified 2026-06-07). The CI migration runner only applies files
-- ADDED in a push, so those historical migrations never ran here; meanwhile the
-- `resources.visual_analysis_status` column and the match_videos_by_embedding RPC
-- (mig 259) already reference this table, so visual analysis is broken at the data
-- layer until the table exists.
--
-- This recreates it directly in its FINAL post-076 form (idempotent), matching
-- app.models.media.ResourceAnalysis exactly so the schema-drift guard passes.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS resource_analysis (
    resource_id BIGINT NOT NULL,
    analysis_level VARCHAR(10) NOT NULL DEFAULT 'none'
        CHECK (analysis_level IN ('none', 'L1', 'L2', 'L3')),
    visual_description TEXT,
    detected_objects JSONB DEFAULT '[]'::jsonb,
    detected_scenes JSONB DEFAULT '[]'::jsonb,
    detected_people JSONB DEFAULT '[]'::jsonb,
    detected_text TEXT,
    full_text_for_embedding TEXT,
    content_embedding vector(1536),
    analysis_model VARCHAR(50),
    analysis_cost NUMERIC(10, 6) DEFAULT 0,
    analyzed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT resource_analysis_pkey PRIMARY KEY (resource_id, analysis_level),
    CONSTRAINT resource_analysis_resource_id_fkey
        FOREIGN KEY (resource_id) REFERENCES resources(id) ON DELETE CASCADE
);

-- IVFFlat cosine index for match_videos_by_embedding (mirrors original mig 014).
CREATE INDEX IF NOT EXISTS idx_resource_analysis_embedding
    ON resource_analysis USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_resource_analysis_level
    ON resource_analysis (analysis_level);

-- RLS mirrors the sibling resource_summaries / resource_transcripts pattern
-- (owner-of-resource manages its rows). The backend uses the service-role key
-- and bypasses RLS; this is defense-in-depth for any non-service-role access.
ALTER TABLE resource_analysis ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Manage analysis of owned resources" ON resource_analysis;
CREATE POLICY "Manage analysis of owned resources"
    ON resource_analysis
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM resources
            WHERE resources.id = resource_analysis.resource_id
              AND resources.creator_id = (SELECT uid())
        )
    );

-- PostgREST schema cache must pick up the new table.
NOTIFY pgrst, 'reload schema';
