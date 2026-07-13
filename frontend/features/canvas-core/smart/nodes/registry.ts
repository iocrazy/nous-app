/**
 * Smart-mode nodeTypes map — passed to `<ReactFlow nodeTypes={...} />`.
 * Memoise the import at module scope so React Flow doesn't recreate
 * its internal node renderers on every render (a known footgun in the
 * RF docs).
 */

import { CharacterNodeView } from './CharacterNodeView';
import { LibEntityNodeView } from './LibEntityNodeView';
import { GroupNodeView } from './GroupNodeView';
import { LlmNodeView } from './LlmNodeView';
import { LoopNodeView } from './LoopNodeView';
import { MediaNodeView } from './MediaNodeView';
import { OutputNodeView } from './OutputNodeView';
import { PromptNodeView } from './PromptNodeView';
import { ShotNodeView } from './ShotNodeView';
import { TimelineNodeView } from './TimelineNodeView';

export const SMART_NODE_TYPES = {
  character: CharacterNodeView,
  location: LibEntityNodeView,
  prop: LibEntityNodeView,
  shot: ShotNodeView,
  media: MediaNodeView,
  prompt: PromptNodeView,
  llm: LlmNodeView,
  group: GroupNodeView,
  timeline: TimelineNodeView,
  output: OutputNodeView,
  loop: LoopNodeView,
} as const;
