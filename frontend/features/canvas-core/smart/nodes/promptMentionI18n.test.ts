// features/canvas-core/smart/nodes/promptMentionI18n.test.ts
//
// en/zh parity for the `@` picker's namespace.
//
// Every `t()` call in the picker passes an English default, so a key missing
// from en.json renders correctly and reports nothing — and a key missing from
// zh.json falls back to English for zh users only, which nobody on an English
// machine ever sees. Reading both locale files is the only way this shows up
// before a user does. Same shape as `assetNodeI18n.test.ts`, and for the same
// reason; a separate file because it guards a separate namespace.
//
// The key list is read out of the SOURCE. A hand-kept inventory drifts the
// moment a string is added, and the drift is silent in exactly the direction
// this test exists to catch.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../../../public/locales');
const SOURCES = [
  path.join(__dirname, 'PromptMentionPicker.tsx'),
  path.join(__dirname, 'PromptNodeView.tsx'),
];

function load(lang: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8'));
}

function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    tree,
  );
}

const usedKeys = [
  ...new Set(
    SOURCES.flatMap((file) => {
      const src = fs.readFileSync(file, 'utf8');
      return [...src.matchAll(/'(canvas\.mention\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]);
    }),
  ),
].sort();

const en = load('en');
const zh = load('zh');

/** A key is translated when it is a string, or when BOTH plural forms are. */
function translated(tree: Record<string, unknown>, key: string): boolean {
  if (typeof at(tree, key) === 'string') return true;
  return (
    typeof at(tree, `${key}_one`) === 'string' &&
    typeof at(tree, `${key}_other`) === 'string'
  );
}

describe('@ picker i18n', () => {
  it('found keys at all — an empty list would pass every case below', () => {
    expect(usedKeys.length).toBeGreaterThan(10);
  });

  it.each(usedKeys)('%s is translated in both locales', (key) => {
    expect(translated(en, key), `${key} missing from en.json`).toBe(true);
    expect(translated(zh, key), `${key} missing from zh.json`).toBe(true);
  });

  it('no locale carries a canvas.mention key nothing asks for', () => {
    const declared = [
      ...new Set(
        Object.keys((at(en, 'canvas.mention') ?? {}) as Record<string, unknown>).map((k) =>
          `canvas.mention.${k.replace(/_(one|other)$/, '')}`,
        ),
      ),
    ].sort();
    expect(declared).toEqual(usedKeys);
  });

  it('the two locales carry exactly the same canvas.mention keys', () => {
    const keys = (tree: Record<string, unknown>) =>
      Object.keys((at(tree, 'canvas.mention') ?? {}) as Record<string, unknown>).sort();
    expect(keys(zh)).toEqual(keys(en));
  });

  it('the zh values are actually translated, not copied English', () => {
    // A copied English value passes a "the key exists" check while leaving zh
    // users on English — the failure this file is really about. Interpolation
    // is exempt: `{{count}} 项结果` and `{{count}} results` differ, but a value
    // that is ONLY a placeholder legitimately matches.
    const keys = Object.keys((at(en, 'canvas.mention') ?? {}) as Record<string, unknown>);
    const copied = keys
      .map((k) => `canvas.mention.${k}`)
      .filter((k) => at(en, k) === at(zh, k));
    expect(copied).toEqual([]);
  });

  it('the bundle drop reasons the mention path can surface are named in both locales', () => {
    // A mention's dropped references are rendered through the PROMPT node's
    // badge (`canvas.refDropReason.*`), while the wired card renders the same
    // codes through `canvas.asset.dropReason.*`. Two badges, one backend
    // vocabulary — so the codes the bundle endpoint can answer must exist in
    // BOTH families or one badge leaks a raw identifier.
    const src = fs.readFileSync(
      path.resolve(__dirname, '../../../../services/assetsService.ts'),
      'utf8',
    );
    const union = /export type DroppedReason =([^;]+);/.exec(src)?.[1] ?? '';
    const reasons = [...union.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]);
    expect(reasons.length).toBeGreaterThan(1);
    for (const reason of reasons) {
      for (const [lang, tree] of [['en', en], ['zh', zh]] as const) {
        expect(
          typeof at(tree, `canvas.refDropReason.${reason}`),
          `${lang} is missing canvas.refDropReason.${reason}`,
        ).toBe('string');
      }
    }
  });
});
