// features/canvas-core/library/promptPanelTarget.test.ts
//
// The naming rule three call sites share. Each case is a way the three used to
// be able to drift apart when each wrote the rule out for itself.

import { describe, expect, it } from 'vitest';

import { promptPanelTarget, titleFromBody } from './promptPanelTarget';

describe('titleFromBody', () => {
  it('names the card by the body first line', () => {
    expect(titleFromBody('a harbour at dusk', 'Prompt')).toBe('a harbour at dusk');
  });

  it('stops at the first newline — the bar is one line high', () => {
    expect(titleFromBody('first line\nsecond line', 'Prompt')).toBe('first line');
  });

  it('cuts at 40 characters', () => {
    const long = 'x'.repeat(60);
    expect(titleFromBody(long, 'Prompt')).toBe('x'.repeat(40));
  });

  it('falls back on an empty body, and on no body at all', () => {
    expect(titleFromBody('', 'Prompt')).toBe('Prompt');
    expect(titleFromBody(undefined, 'Prompt')).toBe('Prompt');
  });

  it('takes the fallback from the caller, so it can be translated', () => {
    // The module has no t() of its own; every caller passes
    // t('canvas.library.untitledPrompt', 'Prompt').
    expect(titleFromBody('', '提示词')).toBe('提示词');
  });
});

describe('promptPanelTarget', () => {
  it('carries the node id and the prompt discriminant beside that name', () => {
    expect(promptPanelTarget('p1', 'a harbour at dusk', 'Prompt')).toEqual({
      nodeId: 'p1',
      kind: 'prompt',
      title: 'a harbour at dusk',
    });
  });

  it('uses the same rule as titleFromBody rather than a second copy of it', () => {
    const long = 'y'.repeat(60);
    expect(promptPanelTarget('p1', long, 'Prompt').title).toBe(titleFromBody(long, 'Prompt'));
  });
});
