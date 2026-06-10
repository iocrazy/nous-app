/**
 * Smart-mode nodeTypes map — passed to `<ReactFlow nodeTypes={...} />`.
 * Memoise the import at module scope so React Flow doesn't recreate
 * its internal node renderers on every render (a known footgun in the
 * RF docs).
 */

import { OutputNodeView } from './OutputNodeView';
import { PromptNodeView } from './PromptNodeView';
import { ShotNodeView } from './ShotNodeView';

export const SMART_NODE_TYPES = {
  shot: ShotNodeView,
  prompt: PromptNodeView,
  output: OutputNodeView,
} as const;
