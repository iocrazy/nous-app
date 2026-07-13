/**
 * M3 item 5 — mention CHIPS as inline decorations (M2 carry-over).
 * The chip is a PM inline decoration over the literal `@Name` text — never a
 * text mutation — carrying the same class/data contract as legacy
 * `buildElementHtml` (`mh-mention` known / `mh-mention unknown` fallback).
 */
import { render, waitFor, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

// jsdom geometry polyfills (mirrors the other tiptap test harnesses).
const zeroRect = { bottom: 0, height: 0, left: 0, right: 0, toJSON: () => ({}), top: 0, width: 0, x: 0, y: 0 };
Element.prototype.getClientRects = () => [] as unknown as DOMRectList;
Element.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
Range.prototype.getClientRects = () => [] as unknown as DOMRectList;
Range.prototype.getBoundingClientRect = () => zeroRect as DOMRect;
if (typeof document.elementFromPoint !== 'function') document.elementFromPoint = () => null;

import { TipTapSceneEditor } from '../tiptap/TipTapSceneEditor';
import type { ScriptElement } from '../types';

const els = (text: string): ScriptElement[] => [
  { id: 'e1', type: 'action', text, character_id: null },
];

describe('M3 — mention chip decorations', () => {
  it('a KNOWN candidate renders the chip class + data-mention, without mutating text', async () => {
    const { container } = render(
      <TipTapSceneEditor
        initialElements={els('Say hi to @Mary now')}
        format="hollywood"
        dispatchOps={() => {}}
        mentionCandidates={['Mary']}
      />,
    );
    await waitFor(() => {
      const chip = container.querySelector('.mh-mention:not(.unknown)');
      expect(chip).not.toBeNull();
      expect(chip?.getAttribute('data-mention')).toBe('Mary');
    });
    // The underlying text is untouched — the literal @Mary survives.
    expect(container.textContent).toContain('@Mary');
  });

  it('an UNKNOWN name gets the grey fallback class', async () => {
    const { container } = render(
      <TipTapSceneEditor
        initialElements={els('ping @Nobody please')}
        format="hollywood"
        dispatchOps={() => {}}
        mentionCandidates={['Mary']}
      />,
    );
    await waitFor(() => {
      expect(container.querySelector('.mh-mention.unknown')).not.toBeNull();
    });
  });

  it('no @ in the text → no chip decorations at all', async () => {
    const { container } = render(
      <TipTapSceneEditor
        initialElements={els('plain action line')}
        format="hollywood"
        dispatchOps={() => {}}
        mentionCandidates={['Mary']}
      />,
    );
    await waitFor(() => expect(container.querySelector('[data-el-id="e1"]')).not.toBeNull());
    expect(container.querySelector('.mh-mention')).toBeNull();
  });

  it('a mentionCandidates prop change re-decorates without a doc edit', async () => {
    const { container, rerender } = render(
      <TipTapSceneEditor
        initialElements={els('enter @Zoe here')}
        format="hollywood"
        dispatchOps={() => {}}
        mentionCandidates={[]}
      />,
    );
    await waitFor(() => {
      expect(container.querySelector('.mh-mention.unknown')).not.toBeNull();
    });
    await act(async () => {
      rerender(
        <TipTapSceneEditor
          initialElements={els('enter @Zoe here')}
          format="hollywood"
          dispatchOps={() => {}}
          mentionCandidates={['Zoe']}
        />,
      );
    });
    await waitFor(() => {
      const chip = container.querySelector('.mh-mention:not(.unknown)');
      expect(chip?.getAttribute('data-mention')).toBe('Zoe');
    });
  });
});
