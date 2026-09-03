// features/canvas-core/smart/promptStrip.test.ts
//
// The strip's claim about the request, pinned against the order
// `generationRunner` actually builds:
//
//     [...assets.reference_urls, ...ctx.source_urls.filter(…)]
//
// i.e. connected asset CARDS, then MENTIONS, then the wired images. The strip
// used to draw the wired images first and number the mentions after them, so
// with `max_refs = 2`, one wired input and two mentions it dimmed the second
// mention while the backend dropped the wired input. Confidently wrong.
//
// The other half of the file is about what this function REFUSES to answer. A
// mention stands for several references behind one thumbnail, and when that
// span is unknown every position after it is unknown too — reported as null
// rather than guessed.
//
// The inputs are VALUES, not the graph: a node view must never subscribe to
// `s.nodes` (see `useGraphDerived.ts`), so the two graph-derived halves arrive
// as a shallow-stable url list and a plain number.

import { describe, expect, it } from 'vitest';

import type { AssetNodeData } from './types';
import type { MentionedAsset } from './mentionedAssets';
import { promptStripEntries, upstreamAssetRefCount } from './promptStrip';

const GM = (n: string) => `/api/v1/generated-media/${n}/cover`;
const RES = (n: string) => `/api/v1/resources/${n}/cover`;

/** `ref_resource_ids` omitted = NOT ASKED, a different state from `[]`. */
const mention = (id: string, refs?: string[]): MentionedAsset => ({
  asset_id: id,
  name: `Asset ${id}`,
  asset_type: 'character',
  cover_file_id: null,
  ...(refs ? { ref_resource_ids: refs } : {}),
});

const card = (selected: string[]): { data: AssetNodeData } =>
  ({
    data: {
      asset_id: '900',
      loadout_id: null,
      selected_file_ids: selected,
      name: 'Wired',
      asset_type: 'character',
      cover_file_id: null,
      readiness_state: 'ready',
    },
  }) as unknown as { data: AssetNodeData };

interface Args {
  mentions?: MentionedAsset[];
  inputUrls?: string[];
  assetRefsAhead?: number;
  maxRefs?: number | null;
}

const strip = ({
  mentions = [],
  inputUrls = [],
  assetRefsAhead = 0,
  maxRefs = null,
}: Args) => promptStripEntries({ mentions, inputUrls, assetRefsAhead, maxRefs });

const kinds = (args: Args) =>
  strip(args).map((e) => (e.kind === 'mention' ? `mention:${e.asset.asset_id}` : `input:${e.url}`));

describe('upstreamAssetRefCount', () => {
  it('sums each card’s checklist', () => {
    expect(upstreamAssetRefCount([card(['1', '2']), card(['3'])], null)).toBe(3);
  });

  it('a card can only send as many as the ceiling lets it', () => {
    // `build_bundle::_restrict_to_selection` applies the provider ceiling
    // WITHIN the card's own selection, which is the same arithmetic the card's
    // row-dimming uses. Counting all five would push the strip off by three.
    expect(upstreamAssetRefCount([card(['1', '2', '3', '4', '5'])], 2)).toBe(2);
  });

  it('no cards is zero, not a guess', () => {
    expect(upstreamAssetRefCount([], 2)).toBe(0);
  });
});

describe('order — mentions lead, wired images follow', () => {
  it('draws asset references before the wired images, like the runner sends them', () => {
    expect(kinds({ mentions: [mention('7', ['70'])], inputUrls: [GM('1')] })).toEqual([
      'mention:7',
      `input:${GM('1')}`,
    ]);
  });

  it('keeps the wired images in the order it was handed', () => {
    const urls = strip({ inputUrls: [GM('1'), GM('2')] })
      .filter((e) => e.kind === 'input')
      .map((e) => (e as Extract<typeof e, { kind: 'input' }>).url);
    expect(urls).toEqual([GM('1'), GM('2')]);
  });

  it('is empty for a prompt with nothing feeding it', () => {
    expect(strip({})).toEqual([]);
  });
});

describe('positions — counted in delivery order', () => {
  it('numbers mentions from 1 and the wired images after their span', () => {
    const entries = strip({ mentions: [mention('7', ['70', '71'])], inputUrls: [GM('1')] });
    expect(entries.map((e) => e.position)).toEqual([1, 3]);
    // ONE tile, TWO references: the wired image lands at 3, not 2.
    expect(entries[0].refCount).toBe(2);
  });

  it('starts after the references the connected cards contribute', () => {
    // The cards are not drawn here — they draw their own checklists — but
    // their references occupy the first positions of the same request.
    const entries = strip({
      mentions: [mention('7', ['70'])],
      inputUrls: [GM('1')],
      assetRefsAhead: 2,
    });
    expect(entries.map((e) => e.position)).toEqual([3, 4]);
  });

  it('the same reference twice is one position', () => {
    const entries = strip({ mentions: [mention('7', ['70']), mention('8', ['70', '80'])] });
    expect(entries.map((e) => e.position)).toEqual([1, 1]);
    // The second mention adds one NEW reference; the shared one is not a
    // second delivery, and counting it twice would shift everything after.
    expect(entries[1].refCount).toBe(2);
  });
});

describe('the provider ceiling', () => {
  it('dims what the tail trim will drop, and nothing else', () => {
    const entries = strip({
      mentions: [mention('7', ['70'])],
      inputUrls: [GM('1'), GM('2')],
      maxRefs: 2,
    });
    expect(entries.map((e) => e.beyondLimit)).toEqual([false, false, true]);
  });

  it('dims the MENTION when the cards ahead already filled the ceiling', () => {
    // This is the inverted case: the old strip dimmed the last mention while
    // the backend was dropping the wired input.
    const entries = strip({ mentions: [mention('7', ['70'])], assetRefsAhead: 2, maxRefs: 2 });
    expect(entries[0].beyondLimit).toBe(true);
  });

  it('does not dim a mention that straddles the ceiling — part of it survives', () => {
    expect(strip({ mentions: [mention('7', ['70', '71', '72'])], maxRefs: 2 })[0].beyondLimit).toBe(
      false,
    );
  });

  it('an unknown ceiling dims NOTHING — null is not zero', () => {
    const entries = strip({ mentions: [mention('7', ['70'])], inputUrls: [GM('1'), GM('2')] });
    expect(entries.map((e) => e.beyondLimit)).toEqual([false, false, false]);
  });
});

describe('what it refuses to answer', () => {
  it('a mention with no snapshot has no position, and neither does anything after it', () => {
    // `ref_resource_ids` absent = NOT ASKED (saved before the field existed, or
    // the detail fetch failed). Its span is unknown, so every position after it
    // would be a fabrication.
    const entries = strip({
      mentions: [mention('7'), mention('8', ['80'])],
      inputUrls: [GM('1')],
      maxRefs: 1,
    });
    expect(entries.map((e) => e.position)).toEqual([null, null, null]);
    expect(entries.map((e) => e.beyondLimit)).toEqual([false, false, false]);
    expect(entries[0].refCount).toBeNull();
  });

  it('an EMPTY snapshot is a real answer, not an unknown one', () => {
    // A `prompt` asset has no primary file slot — it contributes prompt text
    // and no reference images. Dimming it would say the provider dropped
    // something that was never sent; treating it as unknown would blank the
    // positions of everything after it for no reason.
    const entries = strip({ mentions: [mention('7', [])], inputUrls: [GM('1')], maxRefs: 1 });
    expect(entries[0].refCount).toBe(0);
    expect(entries[0].position).toBeNull();
    expect(entries[0].beyondLimit).toBe(false);
    expect(entries[1].position).toBe(1);
  });

  it('a mention resolves to the asset reference URL shape the backend bridges', () => {
    // The wired list carrying the same url is one reference either way.
    const entries = strip({ mentions: [mention('7', ['70'])], inputUrls: [RES('70')] });
    expect(entries.map((e) => e.position)).toEqual([1, 1]);
  });
});
