/**
 * The Outputs tab's state, transport and key routing.
 *
 * Mirrors `useMentionAssetsTab` in shape so the two tabs cannot disagree about
 * what a mention session is; what differs is the population (issue-scoped, and
 * read once per session rather than re-queried per keystroke) and the fact
 * that a failed read has to be SAID.
 *
 * The service is mocked at the module boundary and answers the real wire shape
 * of `GET /api/v1/issues/{id}/outputs`: ids as strings, `versions` newest
 * first, `title` nullable.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

// `vi.hoisted` because `vi.mock` is lifted above every import: a plain const
// here would be read before it is initialised.
const { listIssueOutputs } = vi.hoisted(() => ({ listIssueOutputs: vi.fn() }));

vi.mock('../../services/outputsService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/outputsService')>();
  return { ...actual, listIssueOutputs };
});

import { useMentionOutputsTab } from './useMentionOutputsTab';
import { OutputsError } from '../../services/outputsService';
import type { OutputMentionListHandle } from './OutputMentionList';

const SHOT = {
  kind: 'script_shot',
  ref_id: '727145299382534999',
  title: 'S3 · Shot #1',
  latest_version: 2,
  versions: [
    { id: '1', version: 2, parent_version: 1, run_id: '727145299382534100', issue_id: '727145299382534000', seq: null, turn: null, step: 4, title: 'S3 · Shot #1', model: null, cost_cents: null, created_at: null },
    { id: '2', version: 1, parent_version: null, run_id: '727145299382534100', issue_id: '727145299382534000', seq: null, turn: null, step: 2, title: 'S3 · Shot #1', model: null, cost_cents: null, created_at: null },
  ],
};

/** Stand in for the list `ResourcePickerSuggestion` mounts — the hook holds
 *  the ref, the body fills it in. */
function installHandle(
  outputs: { listRef?: unknown },
  handle: OutputMentionListHandle,
): void {
  (outputs.listRef as { current: OutputMentionListHandle | null }).current = handle;
}

const setup = (over: Partial<Parameters<typeof useMentionOutputsTab>[0]> = {}) =>
  renderHook((props: Parameters<typeof useMentionOutputsTab>[0]) => useMentionOutputsTab(props), {
    initialProps: { pickerOpen: true, issueId: 42, query: '', onSelect: vi.fn(), ...over },
  });

beforeEach(() => {
  listIssueOutputs.mockReset();
  listIssueOutputs.mockResolvedValue([SHOT]);
});

describe('useMentionOutputsTab', () => {
  it('asks for nothing until the tab is actually opened', async () => {
    const { result } = setup();
    expect(listIssueOutputs).not.toHaveBeenCalled();
    // The tab sits in the strip costing nothing — the same posture the asset
    // grid takes, and the reason a hidden tab is rendered rather than unmounted.
    expect(result.current.outputs.active).toBe(false);
  });

  it('reads the issue once when activated, and offers the latest version first', async () => {
    const { result } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    expect(listIssueOutputs).toHaveBeenCalledWith(42);
    expect(result.current.outputs.rows[0]).toMatchObject({ version: 2, latest: true });
  });

  it('does not re-read on every keystroke', async () => {
    // The list is filtered in memory. Re-fetching per character would spend a
    // round trip per keystroke on a population that cannot change mid-session.
    const { result, rerender } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(1));
    rerender({ pickerOpen: true, issueId: 42, query: 'sh', onSelect: vi.fn() });
    rerender({ pickerOpen: true, issueId: 42, query: 'sho', onSelect: vi.fn() });
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    expect(listIssueOutputs).toHaveBeenCalledTimes(1);
  });

  it('filters the loaded rows by the typed query', async () => {
    const { result, rerender } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    rerender({ pickerOpen: true, issueId: 42, query: 'nothing-matches', onSelect: vi.fn() });
    expect(result.current.outputs.rows).toEqual([]);
  });

  it('says a failed read failed, rather than showing an empty shelf', async () => {
    listIssueOutputs.mockRejectedValue(new OutputsError('http_500', 500, 'boom'));
    const { result } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.error).toBeTruthy());
    expect(result.current.outputs.rows).toEqual([]);
    // The server's own prose is never the message.
    expect(result.current.outputs.error).not.toContain('boom');
  });

  it('stops loading once the read fails', async () => {
    listIssueOutputs.mockRejectedValue(new Error('offline'));
    const { result } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.error).toBeTruthy());
    expect(result.current.outputs.loading).toBe(false);
  });

  it('claims nothing at all before a list is mounted', () => {
    // The handle is installed by `ResourcePickerSuggestion` when it renders
    // the body. Until then every key must fall through, or Enter stops making
    // newlines in the reply box while the tab is merely offered.
    const { result } = setup();
    expect(result.current.handleKey('ArrowDown')).toBe(false);
    expect(result.current.handleKey('Enter')).toBe(false);
  });

  it('routes the arrows into the mounted list, and only while active', async () => {
    const { result } = setup();
    const handle = { move: vi.fn(), commitActive: vi.fn(() => true) };
    installHandle(result.current.outputs, handle);

    // Not active yet: the handle exists, and the guard is what refuses.
    expect(result.current.handleKey('ArrowDown')).toBe(false);
    expect(handle.move).not.toHaveBeenCalled();

    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    expect(result.current.handleKey('ArrowDown')).toBe(true);
    expect(handle.move).toHaveBeenCalledWith(1);
    expect(result.current.handleKey('ArrowUp')).toBe(true);
    expect(handle.move).toHaveBeenLastCalledWith(-1);
  });

  it('lets Enter fall through when the list has nothing highlighted', async () => {
    listIssueOutputs.mockResolvedValue([]);
    const { result } = setup();
    // The list answers false when there is no row under the highlight; the
    // hook must forward that false rather than swallowing a newline.
    installHandle(result.current.outputs, { move: vi.fn(), commitActive: vi.fn(() => false) });
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.loading).toBe(false));
    expect(result.current.handleKey('Enter')).toBe(false);
  });

  it('claims nothing while the picker itself is closed', async () => {
    const { result, rerender } = setup();
    const handle = { move: vi.fn(), commitActive: vi.fn(() => true) };
    installHandle(result.current.outputs, handle);
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    rerender({ pickerOpen: false, issueId: 42, query: '', onSelect: vi.fn() });
    expect(result.current.handleKey('ArrowDown')).toBe(false);
    expect(handle.move).not.toHaveBeenCalled();
  });

  it('forgets the tab and the rows on reset, so the next session re-reads', async () => {
    const { result } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    act(() => result.current.reset());
    expect(result.current.outputs.active).toBe(false);
    expect(result.current.outputs.rows).toEqual([]);
    // The issue produces outputs WHILE the composer is open — a cached list
    // from the previous mention session would hide the thing just made.
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(listIssueOutputs).toHaveBeenCalledTimes(2));
  });

  it('leaves the tab without closing the picker on deactivate', async () => {
    const { result } = setup();
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    act(() => result.current.deactivate());
    expect(result.current.outputs.active).toBe(false);
    // Rows survive: the reader switched tabs, they did not end the session.
    expect(result.current.outputs.rows.length).toBe(2);
  });

  it('hands a picked row to the host whole', async () => {
    const onSelect = vi.fn();
    const { result } = setup({ onSelect });
    act(() => result.current.outputs.onActivate());
    await waitFor(() => expect(result.current.outputs.rows.length).toBe(2));
    act(() => result.current.outputs.onSelect(result.current.outputs.rows[1]));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ version: 1, ref_kind: 'script_shot' }));
  });
});
