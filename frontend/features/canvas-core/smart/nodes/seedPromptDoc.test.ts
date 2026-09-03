// Rebuilding a prompt document from what was PERSISTED — the inverse of
// `docToPromptText`.
//
// Only two things survive a reload: the plain-text `body` and the separate
// `image_refs` list. The list carries no position, so restoring a chip means
// finding the `@alias` token the projection left behind and putting the chip
// back where that token sits. Appending instead (the pre-fix behaviour) is
// doubly wrong: the literal `@Image 1` stays in the sentence AND a duplicate
// chip lands after the final full stop — the user sees their prompt rewritten
// simply by reopening the canvas.
//
// The property that pins it is a round trip: text-out / doc-in / text-out must
// be a fixed point, AND the chip must come back at the same offset.

import { describe, expect, it } from 'vitest';
import type { JSONContent } from '@tiptap/core';

import {
  collectImageRefs,
  docToPromptText,
  seedPromptDoc,
  type PromptImageRef,
} from './promptImageRefs';
import type { MentionedAsset } from '../mentionedAssets';

const NO_ASSETS = new Map<string, MentionedAsset>();

const img = (n: number, alias: string): PromptImageRef => ({
  url: `/api/v1/generated-media/${n}/cover`,
  alias,
  kind: 'image',
});

const chipNode = (c: PromptImageRef): JSONContent => ({
  type: 'promptImageRef',
  attrs: { ...c },
});

const asset = (id: string, name: string): MentionedAsset => ({
  asset_id: id,
  name,
  asset_type: 'prop',
  cover_file_id: null,
});

/** A readable projection of the document: one row per block, each entry either
 *  `text:…`, `img:<alias>` or `asset:<id>`. Comparing these rather than raw
 *  tiptap JSON keeps the failure message about POSITION, which is the thing
 *  under test.
 *
 *  Adjacent text nodes are merged: whether a run of prose arrives as one node
 *  or three is a detail of how the seeder walked the string, and ProseMirror
 *  normalises it anyway. Asserting on it would make these tests fail for
 *  reasons that are not the contract. */
function flat(doc: JSONContent): string[][] {
  return (doc.content ?? []).map((block) => {
    const row: string[] = [];
    for (const n of block.content ?? []) {
      if (n.type === 'promptImageRef') row.push(`img:${n.attrs?.alias ?? ''}`);
      else if (n.type === 'promptAssetRef') row.push(`asset:${n.attrs?.asset_id ?? ''}`);
      else {
        const prev = row.length - 1;
        if (prev >= 0 && row[prev].startsWith('text:')) row[prev] += n.text ?? '';
        else row.push(`text:${n.text ?? ''}`);
      }
    }
    return row;
  });
}

describe('seedPromptDoc — a chip returns to the position its token marks', () => {
  it('restores a chip in the middle of the sentence, consuming its token', () => {
    // The reported bug, verbatim: the chip led the line and the body was
    // Chinese, so the alias token is followed immediately by a non-ASCII
    // character rather than a space.
    const chip = img(5, 'Image 1');
    const doc = seedPromptDoc('@Image 1 扩图，旁边放老虎', [chip], NO_ASSETS);

    expect(flat(doc)).toEqual([['img:Image 1', 'text: 扩图，旁边放老虎']]);
    // The literal token must be gone; leaving it is what the user saw.
    expect(docToPromptText(doc)).toBe('@Image 1 扩图，旁边放老虎');
  });

  it('restores a chip that sits between two runs of text', () => {
    const chip = img(7, 'hero.png');
    const doc = seedPromptDoc('make @hero.png brighter', [chip], NO_ASSETS);

    expect(flat(doc)).toEqual([['text:make ', 'img:hero.png', 'text: brighter']]);
  });

  it('is a fixed point: doc → text → doc keeps both the text and the position', () => {
    const chip = img(5, 'Image 1');
    const original: JSONContent = {
      type: 'doc',
      content: [
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: 'make ' },
            chipNode(chip),
            { type: 'text', text: ' brighter, keep the mood' },
          ],
        },
      ],
    };

    const text = docToPromptText(original);
    const reseeded = seedPromptDoc(text, collectImageRefs(original), NO_ASSETS);

    expect(docToPromptText(reseeded)).toBe(text);
    expect(flat(reseeded)).toEqual(flat(original));
  });

  it('does not let @Image 1 match inside @Image 10', () => {
    // `Image N` is how aliases are minted (AttachedComposerPanel), so the
    // prefix collision is a real body, not a contrived one. Iterating chips in
    // list order must still land each on its own token.
    const one = img(1, 'Image 1');
    const ten = img(10, 'Image 10');
    const doc = seedPromptDoc('@Image 10 then @Image 1 done', [one, ten], NO_ASSETS);

    expect(flat(doc)).toEqual([
      ['img:Image 10', 'text: then ', 'img:Image 1', 'text: done'],
    ]);
  });

  it('a chip whose alias is only a prefix of the text token is appended, not spliced', () => {
    // `@Image 10` in the text, but the only chip is `Image 1`. Matching it
    // would silently repoint the reference at the wrong picture; the token
    // stays literal and the chip falls back to today's append.
    const one = img(1, 'Image 1');
    const doc = seedPromptDoc('use @Image 10 here', [one], NO_ASSETS);

    expect(flat(doc)).toEqual([['text:use @Image 10 here', 'img:Image 1']]);
  });

  it('gives each of two same-alias chips its own occurrence', () => {
    // Reachable: aliases are minted from the wired-input index
    // (`Image ${i + 1}`, PromptNodeView), so re-wiring input 0 between two
    // insertions yields two chips that share an alias and differ in url.
    //
    // The POSITION assertion is the load-bearing one. Without the overlap
    // check both chips claim offset 0 and pile up at the head of the line,
    // while the url order stays [a, b] — so a url-only assertion cannot tell
    // the two behaviours apart.
    const a = img(1, 'Image 1');
    const b = { ...img(2, 'Image 1') };
    const doc = seedPromptDoc('@Image 1 and @Image 1', [a, b], NO_ASSETS);

    expect(flat(doc)).toEqual([['img:Image 1', 'text: and ', 'img:Image 1']]);
    const urls = (doc.content?.[0].content ?? [])
      .filter((n) => n.type === 'promptImageRef')
      .map((n) => n.attrs?.url);
    expect(urls).toEqual([a.url, b.url]);
  });

  it('still appends a chip whose token is nowhere in the body', () => {
    // The pre-fix behaviour, kept: the chip is a real reference image, and
    // dropping it would remove a picture from the run.
    const chip = img(5, 'Image 1');
    const doc = seedPromptDoc('no token here', [chip], NO_ASSETS);

    expect(flat(doc)).toEqual([['text:no token here', 'img:Image 1']]);
  });

  it('places nothing and breaks nothing for an empty body', () => {
    expect(flat(seedPromptDoc('', [], NO_ASSETS))).toEqual([[]]);
  });
});

describe('seedPromptDoc — multi-line bodies', () => {
  // `docToPromptText` joins BLOCKS with '\n'. Seeding everything into one
  // paragraph therefore cannot round-trip a two-paragraph prompt: the newline
  // has no block to come from. Splitting on '\n' is the choice pinned here.
  it('splits a newline-separated body back into separate blocks', () => {
    const doc = seedPromptDoc('first line\nsecond line', [], NO_ASSETS);

    expect(flat(doc)).toEqual([['text:first line'], ['text:second line']]);
    expect(docToPromptText(doc)).toBe('first line\nsecond line');
  });

  it('puts each chip in the line whose token names it', () => {
    const one = img(1, 'Image 1');
    const two = img(2, 'Image 2');
    const doc = seedPromptDoc('top @Image 1\nbottom @Image 2', [one, two], NO_ASSETS);

    expect(flat(doc)).toEqual([
      ['text:top ', 'img:Image 1'],
      ['text:bottom ', 'img:Image 2'],
    ]);
  });

  it('is a fixed point across blocks', () => {
    const chip = img(3, 'Image 1');
    const original: JSONContent = {
      type: 'doc',
      content: [
        { type: 'paragraph', content: [{ type: 'text', text: 'wide shot' }] },
        {
          type: 'paragraph',
          content: [
            { type: 'text', text: 'match ' },
            chipNode(chip),
            { type: 'text', text: ' exactly' },
          ],
        },
      ],
    };

    const text = docToPromptText(original);
    const reseeded = seedPromptDoc(text, collectImageRefs(original), NO_ASSETS);

    expect(docToPromptText(reseeded)).toBe(text);
    expect(flat(reseeded)).toEqual(flat(original));
  });

  it('appends an unmatched chip to the last block', () => {
    const chip = img(9, 'Image 9');
    const doc = seedPromptDoc('one\ntwo', [chip], NO_ASSETS);

    expect(flat(doc)).toEqual([['text:one'], ['text:two', 'img:Image 9']]);
  });
});

describe('seedPromptDoc — image chips alongside asset tokens', () => {
  const assets = new Map([['777', asset('777', 'Hero')]]);

  it('restores both chip families at their own positions', () => {
    const chip = img(5, 'Image 1');
    const doc = seedPromptDoc('put @[asset:777] next to @Image 1 please', [chip], assets);

    expect(flat(doc)).toEqual([
      ['text:put ', 'asset:777', 'text: next to ', 'img:Image 1', 'text: please'],
    ]);
  });

  it('never looks for an alias inside an asset token', () => {
    // A body whose asset id happens to contain the alias text must not have
    // its token carved up — the token is opaque storage, not prose.
    const chip = img(5, '777');
    const doc = seedPromptDoc('@[asset:777] and @777', [chip], assets);

    expect(flat(doc)).toEqual([['asset:777', 'text: and ', 'img:777']]);
  });

  it('leaves an unknown asset token literal and still places the image chip', () => {
    const chip = img(5, 'Image 1');
    const doc = seedPromptDoc('@[asset:404] with @Image 1', [chip], assets);

    expect(flat(doc)).toEqual([
      ['text:@[asset:404] with ', 'img:Image 1'],
    ]);
  });
});
