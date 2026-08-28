/**
 * The create-tag dialog must report its own failures.
 *
 * Prod 2026-08-27: a user typed "MiniMax H3", hit 创建 four times, saw nothing
 * happen, gave up and hit 取消 — and only then did a red "Failed to create tag"
 * banner appear on the page behind the (now closed) dialog. The POST had been
 * 409ing every time.
 *
 * Two defects, both covered here:
 *   1. the error went to the page-level banner, which sits UNDER the dialog's
 *      `fixed inset-0 z-50` overlay — invisible while the dialog is open.
 *      (TagsSettings already knew this hazard: the delete-confirm has an
 *      in-dialog variant with a comment saying the list-level one "would
 *      render behind the modal and the user would see nothing". The create
 *      dialog's own error path never got the same treatment.)
 *   2. the message was the hardcoded fallback, so even once visible it did not
 *      say WHY. The backend had answered
 *      `{"error": "Tag 'MiniMax H3' already exists", "code": "http_409"}`.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const fetchAllTags = vi.fn();
const fetchTagGroups = vi.fn();
const updateTag = vi.fn();
const deleteTag = vi.fn();
const createTag = vi.fn();
const createTagGroup = vi.fn();
const renameTagGroup = vi.fn();
const deleteTagGroup = vi.fn();
const reorderTagGroups = vi.fn();

vi.mock('../services/unifiedTagService', () => ({
  fetchAllTags: (...a: unknown[]) => fetchAllTags(...a),
  fetchTagGroups: (...a: unknown[]) => fetchTagGroups(...a),
  updateTag: (...a: unknown[]) => updateTag(...a),
  deleteTag: (...a: unknown[]) => deleteTag(...a),
  createTag: (...a: unknown[]) => createTag(...a),
  createTagGroup: (...a: unknown[]) => createTagGroup(...a),
  renameTagGroup: (...a: unknown[]) => renameTagGroup(...a),
  deleteTagGroup: (...a: unknown[]) => deleteTagGroup(...a),
  reorderTagGroups: (...a: unknown[]) => reorderTagGroups(...a),
}));

// Models the real i18next `t` signature, not just the string-fallback half:
// the component passes `{name, defaultValue}` for the duplicate notice, and a
// mock that treats the 2nd arg as a plain string would render "[object
// Object]" here while the real app renders a sentence — a mock that disagrees
// with the boundary it stands in for.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, opts?: string | Record<string, unknown>) => {
      if (typeof opts === 'string') return opts;
      const dv = opts?.defaultValue;
      if (typeof dv !== 'string') return key;
      return dv.replace(/{{(\w+)}}/g, (_m: string, n: string) => String(opts?.[n] ?? ''));
    },
    i18n: { language: 'zh' },
  }),
}));

import { TagsSettings } from './TagsSettings';

// The real collision from the incident: a tag whose ENGLISH name is
// "MiniMax H3" but which the sidebar renders as "简短" (its name_zh). That
// mismatch is why "MiniMax H3 already exists" reads as nonsense to the user —
// there is no tag by that label anywhere on screen.
const collidingTag = {
  id: '336197082622421',
  name: 'MiniMax H3',
  name_zh: '简短',
  origin: 'curated',
  type: 'user',
  color: null,
  icon: null,
  created_at: '',
};

const openCreateDialog = async () => {
  render(<TagsSettings />);
  await waitFor(() => expect(fetchAllTags).toHaveBeenCalled());
  fireEvent.click(await screen.findByText('settings.tags.createNew'));
};

const typeName = (value: string) => {
  const input = screen
    .getAllByRole('textbox')
    .find((el) => (el as HTMLInputElement).placeholder !== 'Search or create...');
  fireEvent.change(input as HTMLElement, { target: { value } });
  return input as HTMLInputElement;
};

describe('TagsSettings — create dialog error reporting', () => {
  beforeEach(() => {
    fetchAllTags.mockReset().mockResolvedValue([collidingTag]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    createTag.mockReset();
    updateTag.mockReset();
    deleteTag.mockReset();
  });

  it('shows the backend reason INSIDE the dialog and keeps it open', async () => {
    // A name the loaded pool says nothing about — the client pre-check can't
    // catch every collision (stale pool, another scope, a shadow tag), so the
    // server's answer still has to land somewhere the user can read it.
    createTag.mockRejectedValue(new Error("Tag 'Kling' already exists"));
    await openCreateDialog();
    typeName('Kling');

    fireEvent.click(screen.getByText('common.create'));

    // The reason has to be reachable while the dialog is still up — that is
    // the whole failure: the user must not have to cancel to learn why.
    const dialog = await screen.findByRole('dialog');
    await waitFor(() =>
      expect(dialog).toHaveTextContent("Tag 'Kling' already exists"),
    );
    // Still open: a failed create must not look like a successful one.
    expect(screen.queryByRole('dialog')).not.toBeNull();
  });

  it('names the colliding tag the way the user sees it in the list', async () => {
    await openCreateDialog();
    typeName('MiniMax H3');

    // Pre-check off the already-loaded tag pool: no request needed to know
    // this collides. "MiniMax H3" alone is unfindable on screen — the label
    // the user can actually look for is 简短.
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('简短'));
    expect(createTag).not.toHaveBeenCalled();
  });

  it("matches a SYSTEM tag by its Chinese name — the backend does too", async () => {
    // get_tag_by_name checks `name ILIKE ? OR name_zh = ?` for system/time
    // tags, so typing the Chinese half of one really does 409.
    fetchAllTags.mockResolvedValue([
      { ...collidingTag, id: '9', name: 'AI', name_zh: '人工智能', type: 'system' },
    ]);
    await openCreateDialog();
    typeName('人工智能');

    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(dialog).toHaveTextContent('人工智能'));
    expect(createTag).not.toHaveBeenCalled();
  });

  it('does NOT block on a USER tag\'s Chinese name — the backend allows it', async () => {
    // The gate must not out-refuse the server: for user tags the backend
    // matches on the English name only. Blocking here would tell the user
    // "already exists" about a create that would have succeeded.
    createTag.mockResolvedValue({ ...collidingTag, id: '3', name: '简短', name_zh: null });
    await openCreateDialog();
    typeName('简短');

    fireEvent.click(screen.getByText('common.create'));
    await waitFor(() => expect(createTag).toHaveBeenCalled());
  });

  it('lets a genuinely new name through', async () => {
    createTag.mockResolvedValue({ ...collidingTag, id: '2', name: 'Kling', name_zh: null });
    await openCreateDialog();
    typeName('Kling');

    fireEvent.click(screen.getByText('common.create'));
    await waitFor(() => expect(createTag).toHaveBeenCalled());
    expect(createTag.mock.calls[0][0]).toMatchObject({ name: 'Kling' });
  });
});
