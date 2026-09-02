/**
 * Smart-mode nodeTypes map — passed to `<ReactFlow nodeTypes={...} />`.
 * Memoise the import at module scope so React Flow doesn't recreate
 * its internal node renderers on every render (a known footgun in the
 * RF docs).
 *
 * Every view is wrapped in `memo` (Wave 1+2 Task 4 — render cost): React
 * Flow re-creates each node element whenever the flow re-renders, so an
 * unmemoised view re-renders on every drag frame even though its own
 * `id` / `data` / `selected` props are untouched. `memo` alone is not
 * enough — a view that subscribes to the whole `s.nodes` array re-renders
 * from the store side regardless (see `useGraphDerived.ts`), which is why
 * `CanvasSurface.rerender.test.tsx` counts renders instead of asserting
 * the wrapper exists.
 */

import { memo } from 'react';

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

// `location` and `prop` share one view — memoise it once so both entries
// point at the SAME component (two wrappers would remount a node whose
// type flips between them).
const MemoLibEntityNodeView = memo(LibEntityNodeView);

export const SMART_NODE_TYPES = {
  character: memo(CharacterNodeView),
  location: MemoLibEntityNodeView,
  prop: MemoLibEntityNodeView,
  shot: memo(ShotNodeView),
  media: memo(MediaNodeView),
  prompt: memo(PromptNodeView),
  llm: memo(LlmNodeView),
  group: memo(GroupNodeView),
  timeline: memo(TimelineNodeView),
  output: memo(OutputNodeView),
  loop: memo(LoopNodeView),
} as const;
