/**
 * Deleting a note asks through the app's own confirm dialog.
 *
 * Reported with a screenshot of the browser's native `window.confirm` —
 * "app.nous.ink says / 删除这条笔记？" in the browser's chrome, unrelated to
 * everything around it. Every other destructive action in the app goes
 * through `useConfirm` (ConfirmDialog), so this one should read the same.
 *
 * Also pinned: the dialog's answer is what gates the delete. Swapping a
 * synchronous confirm for an async one is exactly where "forgot to await"
 * turns "Cancel" into "delete anyway".
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const listNotes = vi.fn();
const deleteNote = vi.fn();
vi.mock('../services/inspirationService', () => ({
  listNotes: (...a: unknown[]) => listNotes(...a),
  getTagCounts: vi.fn().mockResolvedValue([]),
  getActivity: vi.fn().mockResolvedValue([]),
  deleteNote: (...a: unknown[]) => deleteNote(...a),
  updateNote: vi.fn(),
  createNote: vi.fn(),
  uploadAttachment: vi.fn(),
  deleteAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `http://api.test/att/${id}`,
}));
vi.mock('../services/topicService', () => ({
  getHotspots: vi.fn().mockResolvedValue([]),
  setHotspotState: vi.fn(),
  getHotspot: vi.fn(),
}));
vi.mock('../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));
vi.mock('../components/AILibrary/MarkdownBody', () => ({
  default: ({ source }: { source: string }) => <div>{source}</div>,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback: string) => fallback }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../components/Inspiration/NoteEditor', () => import('../components/Inspiration/testing/noteEditorShim'));

const confirmMock = vi.fn();
vi.mock('../components/ConfirmDialog', () => ({ useConfirm: () => confirmMock }));

import { InspirationPage } from './InspirationPage';

const NOTE = {
  id: '1', content_md: 'first idea', tags: [], ref_hotspot: null,
  pinned: false, rating: 0, note_date: '2026-07-07',
  created_at: '2026-07-07T09:42:00+00:00', updated_at: '2026-07-07T09:42:00+00:00',
  attachments: [],
};

async function openDelete() {
  render(
    <MemoryRouter>
      <InspirationPage />
    </MemoryRouter>,
  );
  const trigger = await screen.findByRole('button', { name: 'Note actions' });
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
}

describe('InspirationPage — delete confirmation', () => {
  beforeEach(() => {
    listNotes.mockReset().mockResolvedValue([NOTE]);
    deleteNote.mockReset().mockResolvedValue(undefined);
    confirmMock.mockReset();
  });

  it('asks through the app dialog, not the browser', async () => {
    const native = vi.spyOn(window, 'confirm');
    confirmMock.mockResolvedValue(true);

    await openDelete();

    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    expect(native).not.toHaveBeenCalled();
    expect(confirmMock.mock.calls[0][0]).toMatchObject({ variant: 'danger' });
    native.mockRestore();
  });

  it('deletes when confirmed', async () => {
    confirmMock.mockResolvedValue(true);
    await openDelete();
    await waitFor(() => expect(deleteNote).toHaveBeenCalledWith('1'));
  });

  it('does not delete when cancelled', async () => {
    confirmMock.mockResolvedValue(false);
    await openDelete();
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    // Let any un-awaited continuation run before asserting it did nothing.
    await new Promise((r) => setTimeout(r, 0));
    expect(deleteNote).not.toHaveBeenCalled();
  });
});
