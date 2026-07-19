/**
 * MemoRail (Beats M4) — the timeline memo-pin rail.
 *
 * Drives the real component with the inspiration notes API stubbed: pins render
 * for anchored notes, hovering the ruler reveals the "+" add affordance, the
 * quick card publishes a note carrying the timeline anchor, and dragging a pin
 * PATCHes a new anchor_sec (a bare click opens edit). Geometry math is covered
 * separately in memoGeometry.test.ts.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Beat } from '../sceneService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, fb?: string) => fb ?? k }),
}));

const toast = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock('../../components/Toast', () => ({ useToast: () => ({ addToast: toast.addToast }) }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ mediaToken: 'tok' }) }));

const svc = vi.hoisted(() => ({
  listAnchoredNotes: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
  uploadAttachment: vi.fn(),
  attachmentUrlWithToken: (id: string) => `att/${id}`,
}));
vi.mock('../../services/inspirationService', () => svc);

import { MemoRail } from '../beats/MemoRail';

const beats: Beat[] = [
  {
    id: 'b1',
    script_id: '1',
    title: 'Setup',
    summary: null,
    scene_ids: [],
    sort_order: 1000,
    start_sec: 0,
    duration_sec: 120,
    beat_role: null,
    color: '#aabbcc',
  },
];

function note(over: Record<string, unknown> = {}) {
  return {
    id: 'n1',
    content_md: 'a captured idea',
    tags: [],
    ref_hotspot: null,
    pinned: false,
    note_date: '2026-07-19',
    anchor_script_id: '1',
    anchor_sec: 30,
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
    attachments: [],
    ...over,
  };
}

function renderRail() {
  return render(
    <MemoRail
      scriptId="1"
      beats={beats}
      pxPerSec={1}
      granularity={1}
      canvasWidth={1000}
      majorTickSecs={[0, 60]}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  svc.listAnchoredNotes.mockResolvedValue([]);
});
afterEach(cleanup);

describe('MemoRail', () => {
  it('renders a pin + card for each anchored note', async () => {
    svc.listAnchoredNotes.mockResolvedValue([note(), note({ id: 'n2', anchor_sec: 90 })]);
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('memo-dot')).toHaveLength(2));
    expect(screen.getAllByTestId('memo-card')).toHaveLength(2);
  });

  it('drops notes with a null anchor_sec (un-pinned)', async () => {
    svc.listAnchoredNotes.mockResolvedValue([note(), note({ id: 'n2', anchor_sec: null })]);
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('memo-dot')).toHaveLength(1));
  });

  it('reveals the "+" add affordance on ruler hover', async () => {
    renderRail();
    await waitFor(() => expect(svc.listAnchoredNotes).toHaveBeenCalled());
    expect(screen.queryByTestId('memo-add')).toBeNull();
    fireEvent.pointerMove(screen.getByTestId('memo-hitzone'), { clientX: 100 });
    const add = await screen.findByTestId('memo-add');
    expect(add.style.left).toBe('100px');
  });

  it('publishes a new memo carrying the timeline anchor', async () => {
    svc.createNote.mockResolvedValue({ ...note(), id: '99' });
    renderRail();
    await waitFor(() => expect(svc.listAnchoredNotes).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('memo-hitzone'), { clientX: 100 });
    const body = await screen.findByTestId('memo-quick-body');
    fireEvent.change(body, { target: { value: 'timeline thought' } });
    fireEvent.click(screen.getByTestId('memo-quick-publish'));
    await waitFor(() => expect(svc.createNote).toHaveBeenCalled());
    const [content, ref, anchor] = svc.createNote.mock.calls[0];
    expect(content).toBe('timeline thought');
    expect(ref).toBeUndefined();
    expect(anchor).toEqual({ scriptId: '1', sec: 100 });
  });

  it('PATCHes a new anchor_sec when a pin is dragged', async () => {
    svc.listAnchoredNotes.mockResolvedValue([note()]); // anchor_sec 30
    svc.updateNote.mockResolvedValue(note({ anchor_sec: 80 }));
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 0, pointerId: 1, button: 0 });
    fireEvent.pointerMove(dot, { clientX: 50, pointerId: 1 }); // +50s → 80
    fireEvent.pointerUp(dot, { clientX: 50, pointerId: 1 });
    await waitFor(() => expect(svc.updateNote).toHaveBeenCalledWith('n1', { anchor_sec: 80 }));
  });

  it('opens the edit card on a bare click (no drag)', async () => {
    svc.listAnchoredNotes.mockResolvedValue([note()]);
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 10, pointerId: 1, button: 0 });
    fireEvent.pointerUp(dot, { clientX: 10, pointerId: 1 }); // unchanged → edit
    const body = await screen.findByTestId('memo-quick-body');
    expect((body as HTMLTextAreaElement).value).toBe('a captured idea');
    expect(svc.updateNote).not.toHaveBeenCalled();
  });

  it('un-pins a memo from the edit card (clears the anchor)', async () => {
    svc.listAnchoredNotes.mockResolvedValue([note()]);
    svc.updateNote.mockResolvedValue({ ...note(), anchor_script_id: null, anchor_sec: null });
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 10, pointerId: 1, button: 0 });
    fireEvent.pointerUp(dot, { clientX: 10, pointerId: 1 });
    fireEvent.click(await screen.findByTestId('memo-quick-unpin'));
    await waitFor(() =>
      expect(svc.updateNote).toHaveBeenCalledWith('n1', {
        anchor_script_id: null,
        anchor_sec: null,
      }),
    );
  });
});
