// components/Distribution/publishDraft.ts
//
// The publish form is ~75 pieces of un-persisted state; leaving the page
// (or opening the library) used to wipe everything, including covers the
// user had just paid to generate. This keeps the fields that are expensive
// or tedious to redo in localStorage, per scope, and restores them on the
// next visit until the post is created or the user discards the draft.
//
// Deliberately NOT everything: nothing that would be wrong to restore later
// (a schedule time in the past, an upload in flight) and nothing large.

export interface PublishDraft {
  savedAt: string;
  contentType?: string;
  selectedVideos?: string[];
  selectedAccounts?: string[];
  title?: string;
  description?: string;
  topics?: string[];
  visibility?: string;
  covers?: { vertical?: string; horizontal?: string } | null;
  selfDeclaration?: string;
  mode?: string;
}

const KEY = (scopeId: string) => `nous.publish-draft:${scopeId}`;

/** A draft worth restoring has at least one of the fields a user types or picks. */
export function draftHasContent(d: Partial<PublishDraft> | null | undefined): boolean {
  if (!d) return false;
  return Boolean(
    (d.selectedVideos && d.selectedVideos.length > 0) ||
      (d.title && d.title.trim()) ||
      (d.description && d.description.trim()) ||
      (d.topics && d.topics.length > 0) ||
      (d.covers && (d.covers.vertical || d.covers.horizontal)) ||
      (d.selectedAccounts && d.selectedAccounts.length > 0),
  );
}

export function readPublishDraft(scopeId: string): PublishDraft | null {
  try {
    const raw = localStorage.getItem(KEY(scopeId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PublishDraft;
    return draftHasContent(parsed) ? parsed : null;
  } catch (err) {
    console.error('[publishDraft] read failed:', err);
    return null;
  }
}

export function writePublishDraft(scopeId: string, draft: Omit<PublishDraft, 'savedAt'>): void {
  try {
    if (!draftHasContent(draft)) {
      localStorage.removeItem(KEY(scopeId));
      return;
    }
    localStorage.setItem(KEY(scopeId), JSON.stringify({ ...draft, savedAt: new Date().toISOString() }));
  } catch (err) {
    console.error('[publishDraft] write failed:', err);
  }
}

export function clearPublishDraft(scopeId: string): void {
  try {
    localStorage.removeItem(KEY(scopeId));
  } catch (err) {
    console.error('[publishDraft] clear failed:', err);
  }
}
