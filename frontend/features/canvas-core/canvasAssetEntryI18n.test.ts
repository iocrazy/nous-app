// features/canvas-core/canvasAssetEntryI18n.test.ts
//
// en/zh parity for the four namespaces P4 Task 6's canvas entry points added:
// `canvas.asAsset.*`, `canvas.projectAssets.*`, `canvas.legacyCard.*` and
// `canvas.assetSeed.*`.
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

const LOCALES = path.resolve(__dirname, '../../public/locales');
const SOURCES = [
  path.join(__dirname, 'smart/nodes/OutputNodeToolbar.tsx'),
  path.join(__dirname, 'smart/nodes/OutputNodeView.tsx'),
  path.join(__dirname, 'smart/nodes/UnmigratedBadge.tsx'),
  path.join(__dirname, 'ui/TopNodeBar.tsx'),
  path.join(__dirname, 'ui/CanvasPage.tsx'),
];

const NAMESPACES = ['asAsset', 'projectAssets', 'legacyCard', 'assetSeed'] as const;

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
    expect(usedKeys.length).toBeGreaterThan(8);
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

  it('the count toasts keep their interpolation placeholders in zh', () => {
    // "Added {{inserted}}" translated without the placeholder renders a
    // sentence with no number in it — a report that reports nothing.
    const placeholders: Record<string, string[]> = {
      'canvas.asAsset.saved': ['name'],
      'canvas.projectAssets.inserted': ['inserted'],
      'canvas.projectAssets.insertedWithSkipped': ['inserted', 'skipped'],
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
