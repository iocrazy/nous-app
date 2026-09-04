// canvas-kit/libraryDrag.ts
//
// The custom MIME a library drag travels in, and the one question the GENERIC
// engine has to ask about it: "is this drag one of ours?".
//
// It lives in the kit rather than in `features/canvas-core/library/` because
// `CanvasEngine` asks that question on every `dragover`, and an engine that
// imports from a feature inverts the layering — the kit is the layer features
// build ON. Only the two primitives move: the payload's SHAPE, how it is
// written and how it is read back all stay with the feature that means
// something by them (`library/dropLibraryItems.ts`, which re-exports these two
// so a reader following the drop path never has to know they live here).

/** The one `dataTransfer` slot a library drag writes. */
export const LIBRARY_DND_MIME = 'application/x-nous-library';

/**
 * Is this drag a library drag?
 *
 * Takes the narrow shape rather than a whole `DataTransfer` so a `dragover`
 * handler can be pinned without one — jsdom builds no real `DataTransfer`,
 * and `types` is the only member this needs.
 */
export function hasLibraryDrag(dt: { types: readonly string[] | DOMStringList }): boolean {
  return Array.from(dt.types ?? []).includes(LIBRARY_DND_MIME);
}
