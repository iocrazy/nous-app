/**
 * ClassicMode node views — MVP assembly (Phase 5a B4).
 *
 * Builds the React Flow `nodeTypes` map consumed by `CanvasSurface` when
 * `kind === 'classic'`. The simple node types (`image` / `prompt` / `llm`
 * / `output`) share one generic view; `comfy` gets its own view with a
 * live elapsed timer + Cancel button.
 *
 * Built once at module scope so React Flow doesn't recreate node
 * renderers on every render.
 */

import { classicNodeDefinitions } from './registry';
import { makeClassicSimpleNodeView } from './nodes/ClassicSimpleNodeView';
import { ComfyNodeView } from './nodes/ComfyNodeView';
import { NoteNodeView } from './nodes/NoteNodeView';
import { PreviewNodeView } from './nodes/PreviewNodeView';
import { GroupNodeView } from './nodes/GroupNodeView';

export const CLASSIC_NODE_TYPES = {
  image: makeClassicSimpleNodeView(classicNodeDefinitions.image),
  prompt: makeClassicSimpleNodeView(classicNodeDefinitions.prompt),
  text: makeClassicSimpleNodeView(classicNodeDefinitions.text),
  llm: makeClassicSimpleNodeView(classicNodeDefinitions.llm),
  output: makeClassicSimpleNodeView(classicNodeDefinitions.output),
  comfy: ComfyNodeView,
  // `note` is a portless sticky — its own minimal view, NOT the port shell.
  note: NoteNodeView,
  // `preview` is a multi-modal display sink — port shell + inline frame.
  preview: PreviewNodeView,
  // `group` is a portless visual container — its own minimal frame view.
  group: GroupNodeView,
} as const;
