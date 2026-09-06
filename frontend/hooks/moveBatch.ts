// frontend/hooks/moveBatch.ts

/**
 * Pure helpers for the folder-mutation paths in `useResourceOperations`
 * (batch Move and inline rename).
 *
 * Two guards stand between a system folder and a mutation, and neither is the
 * context menu (that one only ever sees a single right-clicked folder, so a
 * multi-select batch walks straight past it):
 *
 *   - mig 441 — the folders router answers rename / move / trash / delete on
 *     a system folder with a typed 409 `code=system_folder`.
 *   - mig 457 — a BEFORE UPDATE trigger on `public.folders` raises
 *     `system_folder`, which is what `moveFolder` and `renameFolder` meet:
 *     both write `folders` through PostgREST directly and never reach the
 *     router at all.
 *
 * The caller must not have to know which of those answered. It partitions
 * first so the locked folders are refused out loud before anything is
 * written, and if the server refuses anyway it asks here what the refusal was.
 */

import type { Folder } from '../types';

/**
 * Split a move batch into what the server would accept and what it would
 * refuse with 409 `system_folder` (mig 441 API guard, mig 457 DB guard).
 */
export function partitionMovableFolders(
  folders: readonly Folder[]
): { movable: Folder[]; locked: Folder[] } {
  const movable: Folder[] = [];
  const locked: Folder[] = [];
  for (const folder of folders) {
    if (folder.is_system) locked.push(folder);
    else movable.push(folder);
  }
  return { movable, locked };
}

/** Typed reason for a refused folder mutation (move or rename). */
export type FolderMutationFailureReason = 'system_folder' | 'unknown';

/**
 * Name the reason a folder mutation failed, from whatever the transport threw:
 * an `ApiError` carrying `code: 'system_folder'`, or a PostgREST error whose
 * `message` / `hint` is `system_folder` (mig 457 raises with that message and
 * that HINT).
 *
 * Read structurally rather than with `instanceof`: the two transports on this
 * path throw different classes (`ApiError` from `apiClient`, a plain object
 * from supabase-js) and only the fields overlap.
 *
 * Matched by equality, never as a substring. `system_folder` is a marker we
 * chose, so a real refusal always carries it whole; an unrelated failure whose
 * message merely CONTAINS those characters would otherwise be reported to the
 * user as a system-folder refusal — a wrong answer stated confidently.
 */
export function describeFolderMutationFailure(
  err: unknown
): FolderMutationFailureReason {
  if (typeof err !== 'object' || err === null) return 'unknown';
  const e = err as { code?: unknown; hint?: unknown; message?: unknown };
  for (const field of [e.code, e.hint, e.message]) {
    if (field === 'system_folder') return 'system_folder';
  }
  return 'unknown';
}
