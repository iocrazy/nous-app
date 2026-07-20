/**
 * MemoRail (Beats M5) — the timeline memo rail.
 *
 * Drives the real component with memoService stubbed: pins render for a script's
 * memos, hovering the ruler reveals the "+" add affordance, the quick card
 * creates a memo carrying the timeline anchor, dragging a pin PATCHes a new
 * anchor_sec (a bare click opens edit), and the edit card deletes a memo.
 * Geometry math is covered separately in memoGeometry.test.ts.
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
  listMemos: vi.fn(),
  createMemo: vi.fn(),
  updateMemo: vi.fn(),
  deleteMemo: vi.fn(),
  uploadMemoImage: vi.fn(),
  memoImageUrl: (scriptId: string, memoId: string, idx: number) =>
    `img/${scriptId}/${memoId}/${idx}`,
}));
vi.mock('../beats/memoService', () => svc);

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

function memo(over: Record<string, unknown> = {}) {
  return {
    id: 'n1',
    script_id: '1',
    anchor_sec: 30,
    content: 'a captured idea',
    images: [] as string[],
    created_at: '2026-07-19T00:00:00Z',
    updated_at: '2026-07-19T00:00:00Z',
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
  svc.listMemos.mockResolvedValue([]);
});
afterEach(cleanup);

describe('MemoRail', () => {
  it('renders a pin + card for each memo', async () => {
    svc.listMemos.mockResolvedValue([memo(), memo({ id: 'n2', anchor_sec: 90 })]);
    renderRail();
    await waitFor(() => expect(screen.getAllByTestId('memo-dot')).toHaveLength(2));
    expect(screen.getAllByTestId('memo-card')).toHaveLength(2);
  });

  it('reveals the "+" add affordance on ruler hover', async () => {
    renderRail();
    await waitFor(() => expect(svc.listMemos).toHaveBeenCalled());
    expect(screen.queryByTestId('memo-add')).toBeNull();
    fireEvent.pointerMove(screen.getByTestId('memo-hitzone'), { clientX: 100 });
    const add = await screen.findByTestId('memo-add');
    expect(add.style.left).toBe('100px');
  });

  it('creates a new memo carrying the timeline anchor', async () => {
    svc.createMemo.mockResolvedValue(memo({ id: '99' }));
    renderRail();
    await waitFor(() => expect(svc.listMemos).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId('memo-hitzone'), { clientX: 100 });
    const body = await screen.findByTestId('memo-quick-body');
    fireEvent.change(body, { target: { value: 'timeline thought' } });
    fireEvent.click(screen.getByTestId('memo-quick-publish'));
    await waitFor(() => expect(svc.createMemo).toHaveBeenCalled());
    const [scriptId, payload] = svc.createMemo.mock.calls[0];
    expect(scriptId).toBe('1');
    expect(payload).toEqual({ anchor_sec: 100, content: 'timeline thought', images: [] });
  });

  it('PATCHes a new anchor_sec when a pin is dragged', async () => {
    svc.listMemos.mockResolvedValue([memo()]); // anchor_sec 30
    svc.updateMemo.mockResolvedValue(memo({ anchor_sec: 80 }));
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 0, pointerId: 1, button: 0 });
    fireEvent.pointerMove(dot, { clientX: 50, pointerId: 1 }); // +50s → 80
    fireEvent.pointerUp(dot, { clientX: 50, pointerId: 1 });
    await waitFor(() => expect(svc.updateMemo).toHaveBeenCalledWith('n1', { anchor_sec: 80 }));
  });

  it('opens the edit card on a bare click (no drag)', async () => {
    svc.listMemos.mockResolvedValue([memo()]);
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 10, pointerId: 1, button: 0 });
    fireEvent.pointerUp(dot, { clientX: 10, pointerId: 1 }); // unchanged → edit
    const body = await screen.findByTestId('memo-quick-body');
    expect((body as HTMLTextAreaElement).value).toBe('a captured idea');
    expect(svc.updateMemo).not.toHaveBeenCalled();
  });

  it('deletes a memo from the edit card', async () => {
    svc.listMemos.mockResolvedValue([memo()]);
    svc.deleteMemo.mockResolvedValue(undefined);
    renderRail();
    const dot = await screen.findByTestId('memo-dot');
    fireEvent.pointerDown(dot, { clientX: 10, pointerId: 1, button: 0 });
    fireEvent.pointerUp(dot, { clientX: 10, pointerId: 1 });
    fireEvent.click(await screen.findByTestId('memo-quick-delete'));
    await waitFor(() => expect(svc.deleteMemo).toHaveBeenCalledWith('n1'));
  });
});
