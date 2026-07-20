-- 383_workflow_deliverables.sql — Project Workflow M2-W1 (deliverables chain).
--
-- Two thin columns wire the deliverable loop that spec §2/§7 asks for:
--
--   1. project_stage_nodes.folder_id → the project_folders row that holds a
--      node's filed deliverables. Populated lazily on arrival (best-effort),
--      folder name = node name. advance_service._deliverable_present counts
--      non-trashed files in this folder when set; falls back to the name-match
--      heuristic when null (a node whose folder creation lost the race).
--
--   2. project_files.source_issue_id → the mirror issue a file was filed from
--      (issue-side Deliverables dropzone). Powers the Files module's
--      "from MH-xx" back-link chip and the issue timeline's "filed" line.
--
-- Both FKs ON DELETE SET NULL: dropping a folder / closing-out an issue must
-- never cascade-delete the file or the node instance — the link just clears.
-- RLS is inherited from the parent tables (unchanged here).

BEGIN;

-- ── 1. project_stage_nodes.folder_id ────────────────────────────────────────
ALTER TABLE public.project_stage_nodes
    ADD COLUMN IF NOT EXISTS folder_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'project_stage_nodes_folder_id_fkey'
    ) THEN
        ALTER TABLE public.project_stage_nodes
            ADD CONSTRAINT project_stage_nodes_folder_id_fkey
            FOREIGN KEY (folder_id)
            REFERENCES public.project_folders(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS psn_folder_idx
    ON public.project_stage_nodes (folder_id) WHERE folder_id IS NOT NULL;

-- ── 2. project_files.source_issue_id ────────────────────────────────────────
ALTER TABLE public.project_files
    ADD COLUMN IF NOT EXISTS source_issue_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'project_files_source_issue_id_fkey'
    ) THEN
        ALTER TABLE public.project_files
            ADD CONSTRAINT project_files_source_issue_id_fkey
            FOREIGN KEY (source_issue_id)
            REFERENCES public.issues(id) ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_project_files_source_issue
    ON public.project_files (source_issue_id) WHERE source_issue_id IS NOT NULL;

COMMIT;
