// Image chips inside a prompt body (IC parity: mention-image-token +
// collectMentionedImagesFromPrompt).
//
// IC's prompt box is contenteditable and the reference-image list is SCRAPED
// BACK OUT of the document — a chip in the text IS the reference. That is why
// deleting a chip removes the image from the run. We keep the same contract on
// a tiptap doc: the doc is the source of truth, these helpers read it.

import { describe, expect, it } from 'vitest';

import { collectImageRefs, docToPromptText, mentionQueryFromText } from './promptImageRefs';

const doc = (...content: unknown[]) => ({
  type: 'doc',
  content: [{ type: 'paragraph', content }],
});

const chip = (url: string, alias: string, kind = 'image') => ({
  type: 'promptImageRef',
  attrs: { url, alias, kind },
});

describe('collectImageRefs — the doc is the reference list', () => {
  it('finds a chip among surrounding text, in document order', () => {
    const d = doc(
      { type: 'text', text: 'make ' },
      chip('/api/v1/generated-media/1/cover', 'hero.png'),
      { type: 'text', text: ' brighter than ' },
      chip('/api/v1/generated-media/2/cover', 'Image 2'),
    );

    expect(collectImageRefs(d)).toEqual([
      { url: '/api/v1/generated-media/1/cover', alias: 'hero.png', kind: 'image' },
      { url: '/api/v1/generated-media/2/cover', alias: 'Image 2', kind: 'image' },
    ]);
  });

  it('returns nothing for a doc with no chips', () => {
    expect(collectImageRefs(doc({ type: 'text', text: 'plain prompt' }))).toEqual([]);
  });

  it('dedupes by url — the same image mentioned twice is one reference', () => {
    const d = doc(
      chip('/api/v1/generated-media/1/cover', 'hero.png'),
      { type: 'text', text: ' and again ' },
      chip('/api/v1/generated-media/1/cover', 'hero.png'),
    );
    expect(collectImageRefs(d)).toHaveLength(1);
  });

  it('skips a chip with no url rather than emitting a broken reference', () => {
    const d = doc(chip('', 'ghost'));
    expect(collectImageRefs(d)).toEqual([]);
  });

  it('reads chips nested in any block, not just the first paragraph', () => {
    const d = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'line one' }] },
        { type: 'paragraph', content: [chip('/api/v1/generated-media/9/cover', 'nine')] },
      ],
    };
    expect(collectImageRefs(d).map((r) => r.alias)).toEqual(['nine']);
  });
});

describe('docToPromptText — what the model receives', () => {
  it('renders a chip as its @alias so the prompt reads naturally', () => {
    const d = doc(
      { type: 'text', text: 'brighten ' },
      chip('/api/v1/generated-media/1/cover', 'hero.png'),
      { type: 'text', text: ' please' },
    );
    expect(docToPromptText(d)).toBe('brighten @hero.png please');
  });

  it('joins block-level content with newlines', () => {
    const d = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'one' }] },
        { type: 'paragraph', content: [{ type: 'text', text: 'two' }] },
      ],
    };
    expect(docToPromptText(d)).toBe('one\ntwo');
  });

  it('is empty for an empty doc', () => {
    expect(docToPromptText({ type: 'doc', content: [] })).toBe('');
  });
});

// The rule that keeps the @-mention picker in step with typing. Extracted as a
// pure function because driving a contenteditable through jsdom cannot prove
// it — and this is the behaviour users feel most: the picker narrowing as they
// type, and vanishing the moment they delete the '@'.
describe('mentionQueryFromText', () => {
  it('reports an empty query right after @ is typed', () => {
    expect(mentionQueryFromText('describe @')).toBe('');
  });

  it('reports the word being typed after @', () => {
    expect(mentionQueryFromText('describe @robo')).toBe('robo');
  });

  it('returns null for text with no @ at all', () => {
    expect(mentionQueryFromText('describe a robot')).toBeNull();
  });

  it('returns null once the @ is deleted — this is what closes the picker', () => {
    expect(mentionQueryFromText('describe @robo')).not.toBeNull();
    expect(mentionQueryFromText('describe robo')).toBeNull();
  });

  it('returns null when the token is no longer adjacent to the caret', () => {
    // A space ends the token: '@robo now' is not a live mention any more.
    expect(mentionQueryFromText('@robo now')).toBeNull();
  });

  it('tracks only the last @ when there are several', () => {
    expect(mentionQueryFromText('@one and @two')).toBe('two');
  });
});
