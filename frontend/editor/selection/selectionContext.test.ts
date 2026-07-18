import { describe, expect, it, beforeEach } from 'vitest';
import { resolveSelectionQuote } from './selectionContext';

/**
 * resolveSelectionQuote is the pure core of the "select text → AI chat" pill:
 * given a live Selection, it decides whether the selection is a quotable run of
 * script text and, if so, extracts the plain text + structural context (element
 * id/type, anchor scene, cross-scene flag). The React wrapper only positions a
 * button around whatever this returns.
 */

/** Build a minimal sheet DOM: sheet → scenes → editable rows. */
function mountSheet(): void {
  document.body.innerHTML = `
    <div class="mh-sheet">
      <div class="mh-sheet-inner">
        <div data-testid="scene-block" data-scene-id="s1">
          <div class="mh-scene-heading">INT. OFFICE — DAY</div>
          <div class="mh-el-row">
            <div class="mh-el-editable" data-el-id="e1" data-el-type="action">A quiet room.</div>
          </div>
          <div class="mh-el-row">
            <div class="mh-el-editable" data-el-id="e2" data-el-type="dialogue">Hello there, friend.</div>
          </div>
        </div>
        <div data-testid="scene-block" data-scene-id="s2">
          <div class="mh-scene-heading">EXT. STREET — NIGHT</div>
          <div class="mh-el-row">
            <div class="mh-el-editable" data-el-id="e3" data-el-type="action">Rain falls hard.</div>
          </div>
        </div>
      </div>
    </div>
    <div class="outside-sheet">Unrelated prose not in the sheet.</div>
  `;
}

/** Select a text substring inside the element matched by `selector`. */
function selectWithin(selector: string, start: number, end: number): Selection {
  const el = document.querySelector<HTMLElement>(selector);
  if (!el || !el.firstChild) throw new Error(`no text node in ${selector}`);
  const range = document.createRange();
  range.setStart(el.firstChild, start);
  range.setEnd(el.firstChild, end);
  const sel = window.getSelection();
  if (!sel) throw new Error('no selection');
  sel.removeAllRanges();
  sel.addRange(range);
  return sel;
}

/** Select from a node in one element to a node in another. */
function selectAcross(
  fromSelector: string,
  toSelector: string,
): Selection {
  const from = document.querySelector<HTMLElement>(fromSelector);
  const to = document.querySelector<HTMLElement>(toSelector);
  if (!from?.firstChild || !to?.firstChild) throw new Error('missing endpoints');
  const range = document.createRange();
  range.setStart(from.firstChild, 2);
  range.setEnd(to.firstChild, 4);
  const sel = window.getSelection();
  if (!sel) throw new Error('no selection');
  sel.removeAllRanges();
  sel.addRange(range);
  return sel;
}

describe('resolveSelectionQuote', () => {
  beforeEach(() => {
    mountSheet();
    window.getSelection()?.removeAllRanges();
  });

  it('returns null for a null / collapsed / empty selection', () => {
    expect(resolveSelectionQuote(null)).toBeNull();

    const el = document.querySelector<HTMLElement>('[data-el-id="e1"]')!;
    const range = document.createRange();
    range.setStart(el.firstChild!, 3);
    range.collapse(true); // collapsed caret, no text
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    expect(resolveSelectionQuote(sel)).toBeNull();
  });

  it('extracts text + element/scene context for a selection inside a script line', () => {
    const sel = selectWithin('[data-el-id="e1"]', 2, 7); // "quiet" out of "A quiet room."
    const quote = resolveSelectionQuote(sel);
    expect(quote).not.toBeNull();
    expect(quote!.text).toBe('quiet');
    expect(quote!.elementId).toBe('e1');
    expect(quote!.elementType).toBe('action');
    expect(quote!.sceneId).toBe('s1');
    expect(quote!.sceneLabel).toBe('S1'); // first scene in the sheet
    expect(quote!.crossScene).toBeFalsy();
  });

  it('returns null when the selection is outside the sheet', () => {
    const sel = selectWithin('.outside-sheet', 0, 5);
    expect(resolveSelectionQuote(sel)).toBeNull();
  });

  it('flags a cross-scene selection and anchors to the start scene', () => {
    const sel = selectAcross('[data-el-id="e2"]', '[data-el-id="e3"]');
    const quote = resolveSelectionQuote(sel);
    expect(quote).not.toBeNull();
    // Anchor = the scene where the selection STARTS.
    expect(quote!.sceneId).toBe('s1');
    expect(quote!.sceneLabel).toBe('S1');
    expect(quote!.elementId).toBe('e2');
    expect(quote!.crossScene).toBe(true);
  });
});
