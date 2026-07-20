-- 381_workflow_seed_stage_library.sql — Project Workflow M1 (PR-A).
--
-- Seed the 11-node workflow library into project_stages (upgraded to the node
-- bank in mig 380). Data-only; no schema change. Idempotent via ON CONFLICT
-- (slug) DO UPDATE so re-running keeps the catalog current without duplicating.
--
-- The pre-existing legacy stage rows (planning/generation/review/delivery from
-- mig 295) are left untouched: they keep phase = NULL, and the stage-library
-- endpoint filters to `phase IS NOT NULL`, so only these 11 curated nodes show
-- up in the template editor's "Add from library" picker.
--
-- phase ∈ (pre|production|post|wrap). review_required marks the ✓ acceptance
-- nodes from the spec §2 table; the template seeder mirrors it as the default
-- deliverable gate (overridable per template).

BEGIN;

INSERT INTO public.project_stages
    (slug, name, sort_order, phase, default_role_label, deliverable_label, review_required)
VALUES
    ('script',        'Script',                10, 'pre',        'Writer / Script AI',     'Final script',        true),
    ('storyboard',    'Storyboard',            20, 'pre',        'Artist / Storyboard AI', 'Shot list + boards',  true),
    ('voiceover',     'Voiceover',             30, 'pre',        'VO / TTS AI',            'VO track',            false),
    ('canvas',        'Canvas (AI Generation)',40, 'production', 'Gen AI agent',           'Generated clips',     true),
    ('shooting',      'Shooting',              50, 'production', 'Camera crew',            'Raw footage package', false),
    ('editing',       'Editing',               60, 'post',       'Editor / Edit AI',       'A/B copy',            true),
    ('color-grading', 'Color Grading',         70, 'post',       'Colorist',               'Graded copy',         false),
    ('vfx',           'VFX',                   80, 'post',       'VFX artist',             'VFX shots',           false),
    ('post-delivery', 'Post Delivery',         90, 'post',       'Editor',                 'Final cut upload',    true),
    ('distribution',  'Distribution',         100, 'wrap',       'Ops',                    'Published links',     false),
    ('retrospective', 'Retrospective',        110, 'wrap',       'Whole team',             'Retro report',        false)
ON CONFLICT (slug) DO UPDATE SET
    name               = EXCLUDED.name,
    sort_order         = EXCLUDED.sort_order,
    phase              = EXCLUDED.phase,
    default_role_label = EXCLUDED.default_role_label,
    deliverable_label  = EXCLUDED.deliverable_label,
    review_required    = EXCLUDED.review_required,
    updated_at         = now();

NOTIFY pgrst, 'reload schema';

COMMIT;
