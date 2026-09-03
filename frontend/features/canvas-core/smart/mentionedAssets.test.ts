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
  // A source scan, because the failure is a MISSING call and no behavioural
  // assertion can see one that was never wired.
  //
  // The file list is DERIVED, not typed out. A hardcoded list is blind to the
  // exact case `promptBodyForRun`'s own doc warns about — a FIFTH run site that
  // builds a `RunnerContext` and reads `data.body`. It would compile, pass
  // every test, and ship `@[asset:…]` to the provider.
  //
  // "Builds a run body" is spelled as: mentions `RunnerContext` and is not the
  // module that DEFINES it. That is what every run site has in common and what
  // nothing else in this directory does.
  const DIR = __dirname;
  const DEFINITION = 'runner.ts';

  const runSites = fs
    .readdirSync(DIR)
    .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
    .filter((f) => f !== DEFINITION)
    .filter((f) => fs.readFileSync(path.join(DIR, f), 'utf8').includes('RunnerContext'))
    .sort();

  it('found the run sites at all — an empty scan would pass every case below', () => {
    // A positive control with a floor, not just `> 0`: the four known sites are
    // composer Run/Cascade, chain run, loop round and rerun/retry. A scan that
    // silently matched one file would otherwise look like a passing guard.
    expect(runSites.length).toBeGreaterThanOrEqual(4);
    for (const known of ['CanvasComposer.tsx', 'regenerate.ts', 'chainRun.ts', 'loopRunner.ts']) {
      expect(runSites, `${known} is no longer recognised as a run site`).toContain(known);
    }
  });

  it.each(runSites)('%s renders the body instead of reading the raw field', (file) => {
    const src = fs.readFileSync(path.join(DIR, file), 'utf8');
    expect(src, `${file} never imports the renderer`).toContain('promptBodyForRun');
    // Any `body:` fed straight from a `.body` property, whatever the holder is
    // called. The narrow `(?:data|d)` version this replaced would have missed
    // `body: node.data.body` — the same blindness in a different direction.
    const bare = /\bbody:\s*[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*\.body\b/.exec(src);
    expect(bare?.[0] ?? null, `${file} builds a run body from a raw .body field`).toBeNull();
  });
});
