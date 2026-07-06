/**
 * OutlineView tests (Phase B Task 4).
 *
 * The outline is a same-source read-only tree: chapter title rows (H1-style
 * typographic hierarchy) with their scene rows indented beneath, plus an
 * "Unassigned" group at the bottom for chapterless scenes. Clicking a scene row
 * jumps back to the Script tab; dragging a scene row reorders it within its
 * group (moveScene with a before/after anchor) or reparents it across groups
 * (moveScene with chapter_id). Chapter ids arrive as NATIVE NUMBERS at runtime
 * (#1006) even though typed string — the fixtures below use native ints so the
 * String()-coerced group comparison is exercised for real.
 */
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';
import type { ScriptChapter } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  moveScene: vi.fn(),
}));
vi.mock('../sceneService', () => svc);

import { OutlineView } from '../components/OutlineView';

// chapter_id deliberately a NATIVE NUMBER (as it is at runtime) despite the
// SceneDoc.chapter_id: string | null type — this is the #1006 coercion trap.
const scene = (over: Partial<SceneDoc> & { id: string }): SceneDoc => ({
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  position_x: null,
  position_y: null,
  elements: [{ id: 'el_1', type: 'action', text: 'One pool of light.' }],
  ...over,
});

const chapter = (over: Omit<Partial<ScriptChapter>, 'id'> & { id: unknown }): ScriptChapter =>
  ({
    script_id: '1',
    chapter_number: 1,
    title: 'Act One',
    position_x: 0,
    position_y: 0,
    data_json: {},
    sort_order: 0,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    ...over,
  }) as ScriptChapter;

// jsdom returns a zero rect for everything, which makes the before/after edge
// ambiguous. Pin a real rect so clientY unambiguously picks an edge.
beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    top: 0,
    left: 0,
    right: 200,
    bottom: 40,
    width: 200,
    height: 40,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

const dt = () => ({ setData: vi.fn(), getData: vi.fn(), effectAllowed: '' });

// RTL's synthetic drag events do not carry clientX/Y in jsdom, so dispatch a
// real MouseEvent named 'drop' — React reads clientY off the native event and
// the pinned rect above makes the before/after midpoint deterministic.
function fireDrop(el: HTMLElement, clientY: number) {
  const ev = new MouseEvent('drop', { bubbles: true, cancelable: true, clientY });
  Object.defineProperty(ev, 'dataTransfer', { value: dt(), configurable: true });
  fireEvent(el, ev);
}

describe('OutlineView tree', () => {
  it('renders a group per chapter plus an Unassigned group for chapterless scenes', () => {
    render(
      <OutlineView
        scenes={[
          scene({ id: '200', chapter_id: 100 as unknown as string, sort_order: 0 }),
          scene({ id: '201', chapter_id: null, sort_order: 1 }),
        ]}
        chapters={[chapter({ id: 100 })]}
        onOpenScene={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    // The chapter title reads like a heading; the Unassigned bucket is present.
    expect(screen.getByText('Act One')).toBeInTheDocument();
    expect(screen.getByText('editor.outlineUnassigned')).toBeInTheDocument();
    // Two scene rows, one under each group.
    expect(screen.getAllByTestId('outline-scene-row')).toHaveLength(2);
  });

  it('gives chapter title rows and scene rows distinct typographic classes', () => {
    render(
      <OutlineView
        scenes={[scene({ id: '200', chapter_id: 100 as unknown as string })]}
        chapters={[chapter({ id: 100 })]}
        onOpenScene={vi.fn()}
        onReload={vi.fn()}
      />,
    );
    const title = screen.getByText('Act One');
    const row = screen.getByTestId('outline-scene-row');
    expect(title).toHaveClass('mh-outline-chapter-title');
    expect(row).toHaveClass('mh-outline-scene-row');
    expect(row.className).not.toContain('mh-outline-chapter-title');
  });
});

describe('OutlineView jump', () => {
  it('fires onOpenScene with the scene id when a row is clicked', () => {
    const onOpenScene = vi.fn();
    render(
      <OutlineView
        scenes={[scene({ id: '200', chapter_id: 100 as unknown as string })]}
        chapters={[chapter({ id: 100 })]}
        onOpenScene={onOpenScene}
        onReload={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId('outline-scene-row'));
    expect(onOpenScene).toHaveBeenCalledWith('200');
  });
});

describe('OutlineView drag reorder', () => {
  it('moves a scene within its group with a before/after anchor', async () => {
    svc.moveScene.mockResolvedValue(scene({ id: '200' }));
    const onReload = vi.fn();
    render(
      <OutlineView
        scenes={[
          scene({ id: '200', chapter_id: 100 as unknown as string, sort_order: 0 }),
          scene({ id: '201', chapter_id: 100 as unknown as string, sort_order: 1 }),
        ]}
        chapters={[chapter({ id: 100 })]}
        onOpenScene={vi.fn()}
        onReload={onReload}
      />,
    );
    const [first, second] = screen.getAllByTestId('outline-scene-row');
    fireEvent.dragStart(first, { dataTransfer: dt() });
    // Drop onto the TOP half of the second row → land before it.
    fireDrop(second, 5);

    expect(svc.moveScene).toHaveBeenCalledWith('200', { before_scene_id: '201' });
    await vi.waitFor(() => expect(onReload).toHaveBeenCalled());
  });

  it('reparents a scene across groups with chapter_id (native-int coercion)', async () => {
    svc.moveScene.mockResolvedValue(scene({ id: '201' }));
    const onReload = vi.fn();
    render(
      <OutlineView
        scenes={[
          scene({ id: '200', chapter_id: 100 as unknown as string, sort_order: 0 }),
          scene({ id: '201', chapter_id: 200 as unknown as string, sort_order: 0 }),
        ]}
        chapters={[chapter({ id: 100, title: 'Act One' }), chapter({ id: 200, title: 'Act Two' })]}
        onOpenScene={vi.fn()}
        onReload={onReload}
      />,
    );
    const rows = screen.getAllByTestId('outline-scene-row');
    // Row order: Act One's scene (200), then Act Two's scene (201).
    const dragged = rows[1]; // scene 201, in Act Two (chapter 200)
    const target = rows[0]; // scene 200, in Act One (chapter 100)
    fireEvent.dragStart(dragged, { dataTransfer: dt() });
    // Drop onto the BOTTOM half of the target row → land after it.
    fireDrop(target, 30);

    // Cross-group: carries the target group's chapter_id (native int 100) plus
    // the bottom-half anchor.
    expect(svc.moveScene).toHaveBeenCalledWith('201', {
      chapter_id: 100,
      after_scene_id: '200',
    });
    await vi.waitFor(() => expect(onReload).toHaveBeenCalled());
  });

  it('reparents into an empty chapter group via its header drop target', async () => {
    svc.moveScene.mockResolvedValue(scene({ id: '200' }));
    const onReload = vi.fn();
    render(
      <OutlineView
        scenes={[scene({ id: '200', chapter_id: null })]}
        chapters={[chapter({ id: 100, title: 'Act One' })]}
        onOpenScene={vi.fn()}
        onReload={onReload}
      />,
    );
    const dragged = screen.getByTestId('outline-scene-row'); // chapterless
    const header = within(screen.getByText('Act One').closest('header') as HTMLElement).getByText(
      'Act One',
    );
    fireEvent.dragStart(dragged, { dataTransfer: dt() });
    const headerEl = header.closest('header') as HTMLElement;
    fireEvent.dragOver(headerEl, { dataTransfer: dt() });
    fireEvent.drop(headerEl, { dataTransfer: dt() });

    expect(svc.moveScene).toHaveBeenCalledWith('200', { chapter_id: 100 });
    await vi.waitFor(() => expect(onReload).toHaveBeenCalled());
  });
});
