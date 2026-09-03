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

import { describe, expect, it } from 'vitest';

import type { CanvasConnection, CanvasNode } from '../types';
import type { MentionedAsset } from './mentionedAssets';
import { promptStripEntries } from './promptStrip';

const GM = (n: string) => `/api/v1/generated-media/${n}/cover`;
const RES = (n: string) => `/api/v1/resources/${n}/cover`;

const node = (id: string, type: string, data: Record<string, unknown>): CanvasNode =>
  ({ id, type, position: { x: 0, y: 0 }, data }) as unknown as CanvasNode;

const edge = (source: string, target: string): CanvasConnection =>
  ({ id: `${source}->${target}`, source, target }) as unknown as CanvasConnection;

const mention = (
  id: string,
  refs?: string[],
): MentionedAsset => ({
  asset_id: id,
  name: `Asset ${id}`,
  asset_type: 'character',
  cover_file_id: null,
  ...(refs ? { ref_resource_ids: refs } : {}),
});

/** A media card feeding the prompt — the wired-image source. */
const mediaNode = (id: string, urls: string[]): CanvasNode =>
  node(id, 'media', { title: 'M', items: urls.map((url) => ({ url, kind: 'image' })) });

const assetCard = (id: string, assetId: string, selected: string[]): CanvasNode =>
  node(id, 'asset', {
    asset_id: assetId,
    loadout_id: null,
    selected_file_ids: selected,
    name: 'Wired',
    asset_type: 'character',
    cover_file_id: null,
    readiness_state: 'ready',
  });

const prompt = (mentions: MentionedAsset[] = []): CanvasNode =>
  node('p1', 'prompt', { body: 'x', mentioned_assets: mentions });

const kinds = (nodes: CanvasNode[], conns: CanvasConnection[], maxRefs: number | null) =>
  promptStripEntries('p1', nodes, conns, maxRefs).map((e) =>
    e.kind === 'mention' ? `mention:${e.asset.asset_id}` : `input:${e.url}`,
  );

describe('order — mentions lead, wired images follow', () => {
  it('draws asset references before the wired images, like the runner sends them', () => {
    const nodes = [prompt([mention('7', ['70'])]), mediaNode('m1', [GM('1')])];
    expect(kinds(nodes, [edge('m1', 'p1')], null)).toEqual([
      'mention:7',
      `input:${GM('1')}`,
    ]);
  });

  it('keeps the wired images in their own resolved order', () => {
    const nodes = [prompt(), mediaNode('m1', [GM('1'), GM('2')])];
    const urls = promptStripEntries('p1', nodes, [edge('m1', 'p1')], null)
      .filter((e) => e.kind === 'input')
      .map((e) => (e as Extract<typeof e, { kind: 'input' }>).url);
    expect(urls).toEqual([GM('1'), GM('2')]);
  });

  it('is empty for a prompt with nothing feeding it', () => {
    expect(promptStripEntries('p1', [prompt()], [], null)).toEqual([]);
  });
});

describe('positions — counted in delivery order', () => {
  it('numbers mentions from 1 and the wired images after their span', () => {
    const nodes = [prompt([mention('7', ['70', '71'])]), mediaNode('m1', [GM('1')])];
    const entries = promptStripEntries('p1', nodes, [edge('m1', 'p1')], null);
    expect(entries.map((e) => e.position)).toEqual([1, 3]);
    // ONE tile, TWO references: the wired image lands at 3, not 2.
    expect(entries[0].refCount).toBe(2);
  });

  it('counts a connected asset CARD ahead of everything on the strip', () => {
    // The card is not drawn here — it draws its own checklist — but its
    // references occupy the first positions of the same request.
    const nodes = [
      prompt([mention('7', ['70'])]),
      assetCard('a1', '900', ['10', '11']),
      mediaNode('m1', [GM('1')]),
    ];
    const entries = promptStripEntries(
      'p1',
      nodes,
      [edge('a1', 'p1'), edge('m1', 'p1')],
      null,
    );
    expect(entries.map((e) => e.position)).toEqual([3, 4]);
  });

  it('a card can only send as many as the ceiling lets it', () => {
    // `build_bundle::_restrict_to_selection` applies the provider ceiling
    // WITHIN the card's own selection, which is the same arithmetic the card's
    // row-dimming uses. Counting all five would push the strip off by three.
    const nodes = [prompt([mention('7', ['70'])]), assetCard('a1', '900', ['1', '2', '3', '4', '5'])];
    expect(promptStripEntries('p1', nodes, [edge('a1', 'p1')], 2)[0].position).toBe(3);
  });

  it('the same reference twice is one position', () => {
    const nodes = [prompt([mention('7', ['70']), mention('8', ['70', '80'])])];
    const entries = promptStripEntries('p1', nodes, [], null);
    expect(entries.map((e) => e.position)).toEqual([1, 1]);
    // The second mention adds one NEW reference; the shared one is not a
    // second delivery, and counting it twice would shift everything after.
    expect(entries[1].refCount).toBe(2);
    expect((entries[1] as { kind: 'mention' } & { position: number }).position).toBe(1);
  });
});

describe('the provider ceiling', () => {
  it('dims what the tail trim will drop, and nothing else', () => {
    const nodes = [prompt([mention('7', ['70'])]), mediaNode('m1', [GM('1'), GM('2')])];
    const entries = promptStripEntries('p1', nodes, [edge('m1', 'p1')], 2);
    expect(entries.map((e) => e.beyondLimit)).toEqual([false, false, true]);
  });

  it('dims the MENTION when the cards ahead already filled the ceiling', () => {
    // This is the inverted case: the old strip dimmed the last mention while
    // the backend was dropping the wired input.
    const nodes = [prompt([mention('7', ['70'])]), assetCard('a1', '900', ['1', '2'])];
    const entries = promptStripEntries('p1', nodes, [edge('a1', 'p1')], 2);
    expect(entries[0].beyondLimit).toBe(true);
  });

  it('does not dim a mention that straddles the ceiling — part of it survives', () => {
    const nodes = [prompt([mention('7', ['70', '71', '72'])])];
    const entries = promptStripEntries('p1', nodes, [], 2);
    expect(entries[0].beyondLimit).toBe(false);
  });

  it('an unknown ceiling dims NOTHING — null is not zero', () => {
    const nodes = [prompt([mention('7', ['70'])]), mediaNode('m1', [GM('1'), GM('2')])];
    expect(
      promptStripEntries('p1', nodes, [edge('m1', 'p1')], null).map((e) => e.beyondLimit),
    ).toEqual([false, false, false]);
  });
});

describe('what it refuses to answer', () => {
  it('a mention with no snapshot has no position, and neither does anything after it', () => {
    // `ref_resource_ids` absent = NOT ASKED (saved before the field existed, or
    // the detail fetch failed). Its span is unknown, so every position after it
    // would be a fabrication.
    const nodes = [
      prompt([mention('7'), mention('8', ['80'])]),
      mediaNode('m1', [GM('1')]),
    ];
    const entries = promptStripEntries('p1', nodes, [edge('m1', 'p1')], 1);
    expect(entries.map((e) => e.position)).toEqual([null, null, null]);
    expect(entries.map((e) => e.beyondLimit)).toEqual([false, false, false]);
    expect(entries[0].refCount).toBeNull();
  });

  it('an EMPTY snapshot is a real answer, not an unknown one', () => {
    // A `prompt` asset has no primary file slot — it contributes prompt text
    // and no reference images. Dimming it would say the provider dropped
    // something that was never sent; treating it as unknown would blank the
    // positions of everything after it for no reason.
    const nodes = [prompt([mention('7', [])]), mediaNode('m1', [GM('1')])];
    const entries = promptStripEntries('p1', nodes, [edge('m1', 'p1')], 1);
    expect(entries[0].refCount).toBe(0);
    expect(entries[0].position).toBeNull();
    expect(entries[0].beyondLimit).toBe(false);
    expect(entries[1].position).toBe(1);
  });

  it('a mention resolves to the asset reference URL shape the backend bridges', () => {
    const nodes = [prompt([mention('7', ['70'])]), mediaNode('m1', [RES('70')])];
    // The wired list would carry the same url; it is one reference either way.
    const entries = promptStripEntries('p1', nodes, [edge('m1', 'p1')], null);
    expect(entries.map((e) => e.position)).toEqual([1, 1]);
  });
});
