/**
 * ClassicMode inline node-config field spec (Phase 5a).
 *
 * Maps a runnable/editable node `type` → the minimal set of `data` fields a
 * user must be able to edit so the node can ACTUALLY run. Each field `key` is
 * the EXACT `node.data` key the backend reads — see
 * `backend/app/services/canvas/canvas_run_service.py` (`_extract_image_gen_params`
 * / `_extract_video_gen_params`) and `classic_dispatch.py`
 * (`resolve_provider_slug`). Keys are snake_case to match the `_extract_*`
 * primary lookups (the backend also accepts some camelCase aliases).
 *
 * Backend ↔ key mapping (confirmed):
 *   - image_gen → data.prompt (falls back to run body), data.model,
 *     data.aspect_ratio. (also reads provider_name / reference_image_url —
 *     left out of the MVP field set.)
 *   - video_gen → data.source_image_url (required), data.prompt, data.model.
 *     (also duration_seconds / motion_intensity — out of MVP.)
 *   - comfy    → data.workflow_slug (routes to nous/<slug>); the nous prompt
 *     travels through the run body, which the cascade's `readBody` derives
 *     from data.prompt → so we edit data.prompt here too.
 *   - llm      → data.model (provider_slug override) + the prompt travels
 *     through the run body (cascade `readBody` reads data.prompt).
 *   - prompt / text → passive literal SOURCE nodes. Not piped into consumers
 *     yet (source→consumer data-piping is a FUTURE slice — today every
 *     runnable node reads its OWN data). We still let the content live
 *     somewhere editable: data.prompt for the prompt node, data.text for text.
 */

export type NodeFieldKind = 'text' | 'textarea' | 'number';

export interface NodeFieldSpec {
  /** EXACT node.data key the backend reads. */
  key: string;
  /** Human label (English, Title Case) shown above the input. */
  label: string;
  /** Input flavour. */
  kind: NodeFieldKind;
  /** Placeholder hint. */
  placeholder?: string;
}

/**
 * Per-type editable field sets. MVP/minimal — prompt + the 1-2 params that
 * actually matter for a run, NOT every optional provider param. Types absent
 * from this map (image / output / note / group / preview) render NO editor.
 */
export const CLASSIC_NODE_FIELD_SPECS: Record<string, readonly NodeFieldSpec[]> = {
  image_gen: [
    { key: 'prompt', label: 'Prompt', kind: 'textarea', placeholder: 'Describe the image…' },
    { key: 'model', label: 'Model', kind: 'text', placeholder: 'provider model id (optional)' },
    { key: 'aspect_ratio', label: 'Aspect Ratio', kind: 'text', placeholder: '16:9' },
  ],
  video_gen: [
    {
      key: 'source_image_url',
      label: 'Source Image URL',
      kind: 'text',
      placeholder: 'https://… (required)',
    },
    { key: 'prompt', label: 'Prompt', kind: 'textarea', placeholder: 'Motion / style prompt…' },
    { key: 'model', label: 'Model', kind: 'text', placeholder: 'provider model id (optional)' },
  ],
  comfy: [
    { key: 'workflow_slug', label: 'Workflow', kind: 'text', placeholder: 'nous workflow slug' },
    { key: 'prompt', label: 'Prompt', kind: 'textarea', placeholder: 'Workflow prompt…' },
  ],
  llm: [
    { key: 'prompt', label: 'Prompt', kind: 'textarea', placeholder: 'What should the model do?' },
    { key: 'model', label: 'Model', kind: 'text', placeholder: 'model slug (optional)' },
  ],
  prompt: [
    // Label intentionally NOT the bare node label ("Prompt") to keep the
    // field caption distinct from the node header.
    { key: 'prompt', label: 'Prompt Text', kind: 'textarea', placeholder: 'Prompt text…' },
  ],
  text: [
    { key: 'text', label: 'Text Content', kind: 'textarea', placeholder: 'Static text…' },
  ],
};

/** The editable field set for a node type, or [] when it has no editor. */
export function getNodeFieldSpec(type: string | undefined): readonly NodeFieldSpec[] {
  if (!type) return [];
  return CLASSIC_NODE_FIELD_SPECS[type] ?? [];
}
