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

export const CLASSIC_NODE_TYPES = {
  image: makeClassicSimpleNodeView(classicNodeDefinitions.image),
  prompt: makeClassicSimpleNodeView(classicNodeDefinitions.prompt),
  llm: makeClassicSimpleNodeView(classicNodeDefinitions.llm),
  output: makeClassicSimpleNodeView(classicNodeDefinitions.output),
  comfy: ComfyNodeView,
} as const;
