// features/canvas-core/canvasAssetEntryI18n.test.ts
//
// en/zh parity for the namespaces P4 Task 6's canvas entry points added:
// `canvas.asAsset.*`, `canvas.legacyCard.*` and `canvas.assetSeed.*`.
//
// `canvas.projectAssets.*` was a fourth until the Library panel landed: the
// Project Assets chip that spoke those six lines is gone, and taking a whole
// project shelf is now a scope plus Select All inside the panel. The keys went
// with the code — the "nothing asks for it" case below is what would have
// caught them being left behind.
//
// Every `t()` call in these files passes an English default, so a key missing
// from en.json still renders correctly and reports nothing — and a key missing
// from zh.json falls back to English for zh users only, which nobody on an
// English machine ever sees. Reading both locale files is the only way that
// shows up before a user does.
//
// The key list is read out of the SOURCE, never typed here: a hand-kept
// inventory drifts the moment a string is added, and it drifts silently in
// exactly the direction this file exists to catch.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { NODE_BAR_CHIP_KEYS } from './ui/TopNodeBar';

const LOCALES = path.resolve(__dirname, '../../public/locales');
const SOURCES = [
  path.join(__dirname, 'smart/nodes/OutputNodeToolbar.tsx'),
  path.join(__dirname, 'smart/nodes/OutputNodeView.tsx'),
  path.join(__dirname, 'smart/nodes/UnmigratedBadge.tsx'),
  path.join(__dirname, 'ui/TopNodeBar.tsx'),
  path.join(__dirname, 'ui/CanvasPage.tsx'),
];

const NAMESPACES = ['asAsset', 'legacyCard', 'assetSeed'] as const;

function load(lang: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8'));
}

function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object'
        ? (node as Record<string, unknown>)[part]
        : undefined,
    tree,
  );
}

const pattern = new RegExp(`'(canvas\\.(?:${NAMESPACES.join('|')})\\.[A-Za-z0-9_.]+)'`, 'g');

const usedKeys = [
  ...new Set(
    SOURCES.flatMap((file) =>
      [...fs.readFileSync(file, 'utf8').matchAll(pattern)].map((m) => m[1]),
    ),
  ),
].sort();

const en = load('en');
const zh = load('zh');

describe('canvas asset-entry i18n', () => {
  it('found the keys at all — an empty list would pass every case below', () => {
    // Was 8 while `canvas.projectAssets.*` was still one of the namespaces.
    // The floor is a canary against a broken scan, not a budget, so it moves
    // down with the namespace list rather than pinning a count nobody owns.
    expect(usedKeys.length).toBeGreaterThan(4);
  });

  it.each(usedKeys)('%s is translated in both locales', (key) => {
    expect(typeof at(en, key), `${key} missing from en.json`).toBe('string');
    expect(typeof at(zh, key), `${key} missing from zh.json`).toBe('string');
  });

  it('the zh values are actually translated, not copied English', () => {
    // A copied English value passes a "the key exists" check while leaving zh
    // users on English — the failure this file is really about.
    expect(usedKeys.filter((k) => at(en, k) === at(zh, k))).toEqual([]);
  });

  it('no locale carries a key in these namespaces that nothing asks for', () => {
    const declared = NAMESPACES.flatMap((ns) =>
      Object.keys((at(en, `canvas.${ns}`) ?? {}) as Record<string, unknown>).map(
        (k) => `canvas.${ns}.${k}`,
      ),
    ).sort();
    expect(declared).toEqual(usedKeys);
  });

  it('the two locales carry exactly the same keys in these namespaces', () => {
    for (const ns of NAMESPACES) {
      const keys = (tree: Record<string, unknown>) =>
        Object.keys((at(tree, `canvas.${ns}`) ?? {}) as Record<string, unknown>).sort();
      expect(keys(zh), `canvas.${ns}`).toEqual(keys(en));
    }
  });

  // ── The node bar's chip labels (`canvas.nodeBar.*`, P4 Task 8) ──
  //
  // The strip used to render `chip.label` — a bare English literal — for all
  // ten chips, and P4 added two more the same way. They now go through
  // `t('canvas.nodeBar.' + key, chip.label)`, which means every label still
  // renders in English when a key is missing and reports nothing. The key set
  // is read from `NODE_BAR_CHIP_KEYS`, the array the bar itself maps over, so
  // an eleventh chip fails here until both locales name it.
  it('found the chips at all — an empty list would pass every case below', () => {
    expect(NODE_BAR_CHIP_KEYS.length).toBeGreaterThan(8);
  });

  it.each([...NODE_BAR_CHIP_KEYS])('the %s chip is labelled in both locales', (key) => {
    expect(typeof at(en, `canvas.nodeBar.${key}`), `en ${key}`).toBe('string');
    expect(typeof at(zh, `canvas.nodeBar.${key}`), `zh ${key}`).toBe('string');
  });

  it('no locale carries a chip label the bar does not render', () => {
    const declared = Object.keys(
      (at(en, 'canvas.nodeBar') ?? {}) as Record<string, unknown>,
    ).sort();
    expect(declared).toEqual([...NODE_BAR_CHIP_KEYS].sort());
  });

  it('the chip labels are actually translated, not copied English', () => {
    // `LLM` is legitimately identical in both — it is an acronym we do not
    // localize — and is the one exemption.
    const copied = NODE_BAR_CHIP_KEYS.filter(
      (key) =>
        key !== 'llm' &&
        at(en, `canvas.nodeBar.${key}`) === at(zh, `canvas.nodeBar.${key}`),
    );
    expect(copied).toEqual([]);
  });

  it('the named toasts keep their interpolation placeholders in zh', () => {
    // "Saved to {{name}}" translated without the placeholder renders a
    // sentence naming nothing — a report that reports nothing.
    const placeholders: Record<string, string[]> = {
      'canvas.asAsset.saved': ['name'],
    };
    for (const [key, names] of Object.entries(placeholders)) {
      for (const lang of [en, zh]) {
        const value = at(lang, key) as string;
        for (const name of names) {
          expect(value, `${key} lost {{${name}}}`).toContain(`{{${name}}}`);
        }
      }
    }
  });
});
