/**
 * Auto-translated names must be visibly machine output, not silent input.
 *
 * Prod origin story (2026-08-07, request log):
 *   23:49:25  POST {"name":"MiniMax H3","name_zh":"简短"}       → 201
 *   23:50:15  POST {"name":"MiniMax H3","name_zh":"MiniMax H3"} → 409  ×8
 *
 * The user typed "MiniMax H3"; 600ms later the crowd-sourced translation
 * service answered "简短" and the dialog dropped it into the Chinese field
 * with no indication it was a guess. They hit Create without noticing, so the
 * tag was born with an unrelated Chinese name — and since the Chinese UI
 * renders tags by name_zh, it has been showing up as "简短" ever since,
 * unfindable by the name they gave it. Their eight retries with "= EN" all
 * 409'd against the row they had just created.
 *
 * (The service is a translation MEMORY, not a translator: asked the same
 * question three weeks later it answered "MiniMax H3". For product names it
 * is a dice roll, which is exactly why its output must not look like the
 * user's own typing.)
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

const zhInput = () =>
  screen.getByPlaceholderText('例如：美食、旅行、音乐') as HTMLInputElement;
const enInput = () =>
  screen.getByPlaceholderText('e.g. Food, Travel, Music') as HTMLInputElement;

const openDialog = async () => {
  render(<TagsSettings />);
  await waitFor(() => expect(fetchAllTags).toHaveBeenCalled());
  fireEvent.click(await screen.findByText('settings.tags.createNew'));
};

/** The translation endpoint the component calls directly via fetch. */
const stubTranslation = (translated: string) =>
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ responseData: { translatedText: translated } }),
    } as unknown as Response),
  );

describe('TagsSettings — auto-translated names are marked as machine output', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fetchAllTags.mockReset().mockResolvedValue([]);
    fetchTagGroups.mockReset().mockResolvedValue([]);
    createTag.mockReset().mockResolvedValue({
      id: '1', name: 'x', name_zh: null, type: 'user', origin: 'curated',
      color: null, icon: null, created_at: '',
    });
  });

  it('flags the Chinese field once auto-translate fills it', async () => {
    stubTranslation('简短');
    await openDialog();
    fireEvent.change(enInput(), { target: { value: 'MiniMax H3' } });

    await vi.advanceTimersByTimeAsync(700);
    await waitFor(() => expect(zhInput().value).toBe('简短'));

    // The value arrived from a machine — say so, next to the field.
    const dialog = screen.getByRole('dialog');
    await waitFor(() =>
      expect(dialog).toHaveTextContent('Machine-translated'),
    );
    vi.unstubAllGlobals();
  });

  it('drops the flag once the user edits that field themselves', async () => {
    stubTranslation('简短');
    await openDialog();
    fireEvent.change(enInput(), { target: { value: 'MiniMax H3' } });
    await vi.advanceTimersByTimeAsync(700);
    await waitFor(() => expect(zhInput().value).toBe('简短'));

    fireEvent.change(zhInput(), { target: { value: '迷你麦' } });
    await waitFor(() =>
      expect(screen.getByRole('dialog')).not.toHaveTextContent(
        'Machine-translated',
      ),
    );
    vi.unstubAllGlobals();
  });

  it('drops the flag when "= EN" overrides the guess', async () => {
    stubTranslation('简短');
    await openDialog();
    fireEvent.change(enInput(), { target: { value: 'MiniMax H3' } });
    await vi.advanceTimersByTimeAsync(700);
    await waitFor(() => expect(zhInput().value).toBe('简短'));

    fireEvent.click(screen.getByText('= EN'));
    await waitFor(() => expect(zhInput().value).toBe('MiniMax H3'));
    expect(screen.getByRole('dialog')).not.toHaveTextContent(
      'Machine-translated',
    );
    vi.unstubAllGlobals();
  });

  it('ignores a response for text the field no longer holds', async () => {
    // The 600ms timer can be cancelled; a request already in flight cannot.
    // The zh|en branch wrote its answer into the English box unconditionally
    // — no "only if empty", no check that the box still held the text that
    // was translated — so a late answer landed on top of the user's typing.
    stubTranslation('brief');
    await openDialog();
    fireEvent.change(enInput(), { target: { value: '简短' } });
    await vi.advanceTimersByTimeAsync(700);
    // User retypes while the request is out; their text must win.
    fireEvent.change(enInput(), { target: { value: 'MiniMax H3' } });
    await vi.advanceTimersByTimeAsync(2000);

    await waitFor(() => expect(enInput().value).toBe('MiniMax H3'));
    vi.unstubAllGlobals();
  });
});
