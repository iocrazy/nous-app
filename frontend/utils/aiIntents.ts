/**
 * The three AI-pipeline tags, addressed by their stable `slug` (mig 467).
 *
 * This used to be the GROUP NAME `'Pipeline'`, and pickers hid the group. Two
 * things were wrong with that: a group name is user-editable like any other
 * label, and dragging a tag out of the group put it back in the ordinary
 * pickers while it still triggered the AI. A slug is written only by the
 * migration that seeds these tags — no UI and no request body can change it —
 * so it survives every rename and regroup the user is now free to make.
 */
export const PIPELINE_TAG_SLUGS = ['transcript', 'summary', 'analyze'] as const;

export type PipelineTagSlug = (typeof PIPELINE_TAG_SLUGS)[number];

/** True for the three tags the AI intents own. Pickers hide these. */
export function isPipelineTag(tag: { slug?: string | null }): boolean {
  return !!tag.slug && (PIPELINE_TAG_SLUGS as readonly string[]).includes(tag.slug);
}
