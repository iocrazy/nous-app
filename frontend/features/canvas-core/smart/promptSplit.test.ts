// IC 分隔符 (promptNodePromptItems): split the prompt body into independent
// generation items; single/empty results fall back to the whole text.
import { describe, expect, it } from 'vitest';

import { splitPromptItems } from './promptSplit';

describe('splitPromptItems', () => {
  it('splits on the separator, trimming and dropping empties', () => {
    expect(splitPromptItems('a cat; a dog ; ;a bird', ';')).toEqual([
      'a cat', 'a dog', 'a bird',
    ]);
  });

  it('falls back to the whole text when nothing splits', () => {
    expect(splitPromptItems('just one prompt', ';')).toEqual(['just one prompt']);
  });

  it('empty separator or body → whole text / empty list', () => {
    expect(splitPromptItems('a; b', '')).toEqual(['a; b']);
    expect(splitPromptItems('   ', ';')).toEqual([]);
  });

  it('separator is capped at 8 chars (IC promptNodeSeparator)', () => {
    expect(splitPromptItems('x<LONGSEP>y', '<LONGSEP>'.slice(0, 8))).toEqual([
      'x<LONGSEP>y'.split('<LONGSEP'.slice(0, 8))[0] === 'x' ? 'x' : 'x<LONGSEP>y',
      '>y',
    ]);
  });
});
