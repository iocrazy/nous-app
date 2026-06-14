/**
 * ClassicMode node views — MVP assembly (Phase 5a B4 + path B + W3).
 *
 * Builds the React Flow `nodeTypes` map consumed by `CanvasSurface` when
 * `kind === 'classic'`. The simple/passive node types (`image` / `prompt` /
 * `text` / `video` / `llm` / `output`) share one generic view; the
 * long-running AI-op types (`comfy` / `image_gen` / `video_gen`) share the
 * runnable-op view with a live elapsed timer + Cancel button + inline result
 * thumbnail. W3 transform nodes (`text_join`) get dedicated views.
 *
 * Built once at module scope so React Flow doesn't recreate node
 * renderers on every render.
 */

import { classicNodeDefinitions } from './registry';
import { makeClassicSimpleNodeView } from './nodes/ClassicSimpleNodeView';
import { makeRunnableOpNodeView } from './nodes/RunnableOpNodeView';
import { NoteNodeView } from './nodes/NoteNodeView';
import { PreviewNodeView } from './nodes/PreviewNodeView';
import { GroupNodeView } from './nodes/GroupNodeView';
import { TextJoinNodeView } from './nodes/TextJoinNodeView';

export const CLASSIC_NODE_TYPES = {
  image: makeClassicSimpleNodeView(classicNodeDefinitions.image),
  prompt: makeClassicSimpleNodeView(classicNodeDefinitions.prompt),
  text: makeClassicSimpleNodeView(classicNodeDefinitions.text),
  // W3: passive video URL source — same generic shell as `image`.
  video: makeClassicSimpleNodeView(classicNodeDefinitions.video),
  llm: makeClassicSimpleNodeView(classicNodeDefinitions.llm),
  output: makeClassicSimpleNodeView(classicNodeDefinitions.output),
  // Runnable AI-op nodes — elapsed timer + Cancel + result thumbnail.
  comfy: makeRunnableOpNodeView(classicNodeDefinitions.comfy, 'comfy'),
  image_gen: makeRunnableOpNodeView(classicNodeDefinitions.image_gen, 'image-gen'),
  video_gen: makeRunnableOpNodeView(classicNodeDefinitions.video_gen, 'video-gen'),
  // W3 transform: text_join — dedicated view with inline join preview.
  text_join: TextJoinNodeView,
  // `note` is a portless sticky — its own minimal view, NOT the port shell.
  note: NoteNodeView,
  // `preview` is a multi-modal display sink — port shell + inline frame.
  preview: PreviewNodeView,
  // `group` is a portless visual container — its own minimal frame view.
  group: GroupNodeView,
} as const;
