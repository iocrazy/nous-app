/**
 * PromptNodeView — IME composition guard (2026-08-18 incident: typing
 * Chinese let raw pinyin land in the store mid-composition; the async
 * store→props round-trip then rewrote the controlled value and broke the
 * composition, so letters committed as text).
 *
 * Contract: while composing, keystrokes update only the LOCAL draft (the
 * store body is untouched and the @-picker never opens); compositionend
 * commits the final text once.
 *
 * The body is a tiptap contenteditable now, so `fireEvent.change` cannot
 * drive it and there is no `.value` to read — ProseMirror syncs from the DOM.
 * `typeRaw` writes the text and fires `input`, which is what a keystroke ends
 * up doing. The contract itself is unchanged.
 */

import { ReactFlowProvider } from '@xyflow/react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { PromptNodeView } from './PromptNodeView';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) => (typeof d === 'string' ? d : k),
  }),
}));
vi.mock('../../../../hooks/useResourceSearch', () => ({
  useResourceSearch: vi.fn(() => ({
    data: {
      results: [],
      counts: { all: 0, video: 0, image: 0, doc: 0, audio: 0, pdf: 0 },
      next_cursor: null,
    },
    loading: false,
  })),
}));

const baseProps = {
  selected: false, dragging: false, zIndex: 0, isConnectable: true,
  positionAbsoluteX: 0, positionAbsoluteY: 0, deletable: true,
  draggable: true, selectable: true,
} as const;

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: [
      {
        id: 'p1',
        type: 'prompt',
        position: { x: 0, y: 0 },
        data: {
          body: '',
          provider_slug: '',
          agent_id: null,
          run_status: 'idle',
          resource_refs: [],
        },
      } as unknown as CanvasNode,
    ],
    connections: [],
    selection: [],
  });
}

function storeBody(): string {
  return (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as {
    data: { body: string };
  }).data.body;
}

beforeEach(seed);
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

/** Drive the contenteditable the way a keystroke does. */
function typeRaw(el: HTMLElement, text: string): void {
  const block = el.querySelector('p');
  if (!block) throw new Error('prompt body has no paragraph');
  block.textContent = text;
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

function renderPrompt(): HTMLElement {
  const data = (useCanvasCoreStore
    .getState()
    .nodes.find((n) => (n as { id: string }).id === 'p1') as { data: unknown }).data;
  render(
    <ReactFlowProvider>
      <PromptNodeView {...baseProps} id="p1" type="prompt" data={data as never} />
    </ReactFlowProvider>,
  );
  return screen.getByRole('textbox', { name: 'Prompt body' });
}

describe('IME composition guard', () => {
  it('mid-composition keystrokes stay local; compositionend commits once', async () => {
    const ta = renderPrompt();
    fireEvent.compositionStart(ta);
    typeRaw(ta, 'zhe');
    // The editor shows the intermediate pinyin, the store does not.
    expect(ta.textContent).toContain('zhe');
    expect(storeBody()).toBe('');
    typeRaw(ta, '这是');
    expect(storeBody()).toBe('');
    fireEvent.compositionEnd(ta);
    await waitFor(() => expect(storeBody()).toBe('这是'));
  });

  it('composing an @ never opens the mention picker', () => {
    const ta = renderPrompt();
    fireEvent.compositionStart(ta);
    fireEvent.keyDown(ta, { key: '@' });
    typeRaw(ta, '@');
    expect(screen.queryByTestId('canvas-mention-picker')).toBeNull();
    expect(screen.queryByTestId('mention-tab-library')).toBeNull();
    fireEvent.compositionEnd(ta);
  });

  it('plain (non-IME) typing still writes through immediately', async () => {
    const ta = renderPrompt();
    typeRaw(ta, 'hello');
    await waitFor(() => expect(storeBody()).toBe('hello'));
  });
});
