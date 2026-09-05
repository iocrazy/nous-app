// features/canvas-core/library/mentionHandles.ts
//
// How the PANEL reaches a prompt node's body editor.
//
// The `⌥`-drop path never needed this: the drop lands ON the card, so
// `PromptNodeView` hands its own `bodyEditorRef.current` straight to
// `useLibraryMention`. The Library panel is a different component in a
// different corner of the screen — it knows the target's node id and nothing
// else, and the editor handle is an imperative object that exists only inside
// that card's render.
//
// A MODULE-LEVEL MAP rather than state in `libraryStore` / `canvasCoreStore`,
// deliberately:
//   - these are live imperative objects, not data. Putting one in a zustand
//     store would make it a subscription dependency (every registration
//     re-rendering every subscriber) and would invite it into anything that
//     serialises store state. It must never reach `nodes_json`, undo history,
//     or a dirty check.
//   - it is keyed by node id and read exactly once, at commit time. There is
//     nothing to re-render when it changes.
//
// Registration is scoped to a MOUNT, not to a node id: `registerMentionHandle`
// hands back the undo for the registration it just made, and that undo is a
// no-op once something else has taken the slot. React runs a remount's effect
// setup BEFORE the old effect's cleanup, so a cleanup that deleted by id alone
// would evict the live handle — and the panel would then refuse to insert into
// a card that is mounted and typing.

import type { MentionInserters } from './mentionLibraryItems';

/**
 * The one failure this registry can cause: a handle whose card is registered
 * but whose body editor is not mounted right now (the surface culls off-viewport
 * cards). `PromptNodeView`'s wrappers throw THIS, and `mentionLibraryItems`
 * maps it to the `editor_gone` reason — any other throw from an inserter is a
 * different defect and must not borrow that name.
 */
export class EditorGoneError extends Error {
  constructor(message = 'prompt body editor is not mounted') {
    super(message);
    this.name = 'EditorGoneError';
  }
}

const handles = new Map<string, MentionInserters>();

/**
 * Publish a node's inserters and get back the undo for THIS registration.
 *
 * Safe to call again for the same id (the newest wins) and safe to undo twice.
 */
export function registerMentionHandle(nodeId: string, handle: MentionInserters): () => void {
  handles.set(nodeId, handle);
  return () => {
    // Identity, not just the key — see the header. A stale cleanup must not
    // take out the registration that replaced it.
    if (handles.get(nodeId) === handle) handles.delete(nodeId);
  };
}

/**
 * The node's inserters, or `null` when that card is not mounted.
 *
 * `null` is a real answer the caller must SPEAK, not a reason to do nothing:
 * the surface culls off-viewport nodes, so a target the user aimed at ten
 * seconds ago can genuinely have no editor right now.
 */
export function getMentionHandle(nodeId: string): MentionInserters | null {
  return handles.get(nodeId) ?? null;
}
