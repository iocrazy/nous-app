// features/canvas-core/smart/mentionedAssets.test.ts
//
// The storage form of an `@`-mentioned asset, and the one rule that matters
// about it: the token is how the body REMEMBERS which asset was named, and it
// must never be what the model READS.
//
// The guard at the bottom is the other half. Four run sites build their own
// `RunnerContext`, and reading `data.body` at any of them ships `@[asset:…]`
// to the provider — a defect that is invisible in a diff, invisible in a
// passing test, and only visible in generated output nobody compares. So the
// read is named and the source is scanned for the bare field.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import {
  assetMentionToken,
  mentionedAssetIds,
  promptBodyForRun,
  pruneMentionedAssets,
  renderMentionText,
  splitMentionSegments,
  type MentionedAsset,
} from './mentionedAssets';

const AVA: MentionedAsset = {
  asset_id: '727145299382534201',
  name: 'Ava',
  asset_type: 'character',
  cover_file_id: '727145299382534301',
};
const ALLEY: MentionedAsset = {
  asset_id: '727145299382534202',
  name: 'Back Alley',
  asset_type: 'location',
  cover_file_id: null,
};

describe('the mention token', () => {
  it('round-trips a snowflake id', () => {
    const text = `a shot of ${assetMentionToken(AVA.asset_id)} at dusk`;
    expect(mentionedAssetIds(text)).toEqual([AVA.asset_id]);
  });

  it('splits a body into text runs and tokens, in order', () => {
    const text = `${assetMentionToken(AVA.asset_id)} meets ${assetMentionToken(ALLEY.asset_id)}`;
    expect(splitMentionSegments(text)).toEqual([
      { kind: 'asset', assetId: AVA.asset_id },
      { kind: 'text', text: ' meets ' },
      { kind: 'asset', assetId: ALLEY.asset_id },
    ]);
  });

  it('does not swallow the older @Image N convention', () => {
    // Both live in the same text. The image alias is bare, the asset token is
    // bracketed; a matcher that ate `@Image 1` would silently delete a
    // reference at run time.
    const text = `@Image 1 beside ${assetMentionToken(AVA.asset_id)}`;
    expect(mentionedAssetIds(text)).toEqual([AVA.asset_id]);
    expect(renderMentionText(text, [AVA])).toBe('@Image 1 beside @Ava');
  });

  it('reports each id once, in reading order', () => {
    const t = assetMentionToken(AVA.asset_id);
    expect(mentionedAssetIds(`${t} and ${t}`)).toEqual([AVA.asset_id]);
  });

  it('a fresh matcher every call — two reads of the same text agree', () => {
    // A shared `/g` RegExp carries `lastIndex`, so the second caller would skip
    // matches depending on who ran first.
    const text = `${assetMentionToken(AVA.asset_id)} x ${assetMentionToken(ALLEY.asset_id)}`;
    expect(mentionedAssetIds(text)).toEqual(mentionedAssetIds(text));
    expect(mentionedAssetIds(text)).toHaveLength(2);
  });
});

describe('pruneMentionedAssets', () => {
  it('keeps only what the text still names, in the text’s order', () => {
    const text = `${assetMentionToken(ALLEY.asset_id)} then ${assetMentionToken(AVA.asset_id)}`;
    expect(pruneMentionedAssets(text, [AVA, ALLEY])).toEqual([ALLEY, AVA]);
  });

  it('a deleted token drops its entry', () => {
    expect(pruneMentionedAssets('nothing here', [AVA, ALLEY])).toEqual([]);
  });
});

describe('renderMentionText — what the model receives', () => {
  it('replaces every token with the asset’s name', () => {
    const text = `${assetMentionToken(AVA.asset_id)} walks into ${assetMentionToken(ALLEY.asset_id)}`;
    expect(renderMentionText(text, [AVA, ALLEY])).toBe('@Ava walks into @Back Alley');
  });

  it('never leaves a raw token behind', () => {
    const text = `${assetMentionToken(AVA.asset_id)} x`;
    expect(renderMentionText(text, [AVA])).not.toContain('@[asset:');
  });

  it('drops a token nobody can name rather than leaking the id', () => {
    // Only reachable by pasting a token. Removing it matches what the run
    // does: an unknown id contributes no bundle either, so leaving the text in
    // would describe a reference that is not being sent.
    const text = `before ${assetMentionToken('999')} after`;
    expect(renderMentionText(text, [AVA])).toBe('before  after');
  });

  it('leaves a body with no mentions untouched', () => {
    expect(renderMentionText('a plain prompt', [])).toBe('a plain prompt');
  });
});

describe('promptBodyForRun', () => {
  it('renders the node’s own body against its own record', () => {
    expect(
      promptBodyForRun({
        body: `${assetMentionToken(AVA.asset_id)} at dusk`,
        mentioned_assets: [AVA],
      }),
    ).toBe('@Ava at dusk');
  });

  it('tolerates a node saved before the field existed', () => {
    expect(promptBodyForRun({ body: 'legacy' })).toBe('legacy');
    expect(promptBodyForRun({})).toBe('');
  });
});

describe('every run site reads the body through promptBodyForRun', () => {
  // A source scan, because the failure is a MISSING call and no assertion
  // about behaviour can see one that was never wired. Each of these files
  // builds a `RunnerContext` for a real run path.
  const SITES = [
    'CanvasComposer.tsx',
    'regenerate.ts',
    'chainRun.ts',
    'loopRunner.ts',
  ];

  it.each(SITES)('%s does not read data.body directly', (file) => {
    const src = fs.readFileSync(path.resolve(__dirname, file), 'utf8');
    expect(src, `${file} never imports the renderer`).toContain('promptBodyForRun');
    // `body: data.body` / `body: d.body ?? ''` — the shapes this file's history
    // actually contains. A match means a run site went back to the raw field.
    const bare = /\bbody:\s*(?:data|d)\.body\b/.exec(src);
    expect(bare?.[0] ?? null, `${file} builds a run body from the raw field`).toBeNull();
  });

  it('found real files — an empty scan would pass every case above', () => {
    for (const file of SITES) {
      expect(fs.existsSync(path.resolve(__dirname, file)), file).toBe(true);
    }
  });
});
