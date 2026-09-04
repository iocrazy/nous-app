// frontend/components/assets/saveAsAssetI18n.test.ts
//
// en/zh parity for the keys the "As Asset" entry added (P6 ruling E).
//
// Every `t()` in this feature passes an English default, so a key missing from
// en.json still renders correctly and reports nothing, while a key missing from
// zh.json falls back to English for zh users only — which nobody on an English
// machine ever sees. Both gaps are silent, which is why this reads the locale
// files directly instead of rendering.
//
// The refusal list comes from the service's mirror of the backend router, so
// adding a code there without writing the sentence a user reads fails here.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { RESOURCE_SAVE_AS_ASSET_ERROR_CODES } from '../../services/generatedService';

const LOCALES = path.resolve(__dirname, '../../public/locales');

function load(lang: string): Record<string, unknown> {
  return JSON.parse(fs.readFileSync(path.join(LOCALES, `${lang}.json`), 'utf8'));
}

const en = load('en');
const zh = load('zh');

function at(tree: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined,
    tree,
  );
}

const MENU_KEYS = [
  // The context-menu entry itself.
  'resources.saveAsAsset',
  // The dialog's "where this file came from" line, the resource variant's
  // stand-in for a generation's `source.label`.
  'resources.saveAsAssetSource',
];

describe('As Asset i18n', () => {
  it.each(MENU_KEYS)('%s is translated in both locales', (key) => {
    expect(typeof at(en, key)).toBe('string');
    expect(typeof at(zh, key)).toBe('string');
    expect(at(en, key)).not.toBe('');
    expect(at(zh, key)).not.toBe('');
  });

  it.each(RESOURCE_SAVE_AS_ASSET_ERROR_CODES)(
    'saveAsAsset.err.%s has copy in both locales',
    (code) => {
      const key = `saveAsAsset.err.${code}`;
      expect(typeof at(en, key)).toBe('string');
      expect(typeof at(zh, key)).toBe('string');
    },
  );

  it('every refusal reads as its own answer, not a duplicate of another', () => {
    // Two codes sharing one sentence is the same failure as having no sentence:
    // the user is told something true of a different situation. In particular
    // `resource_not_found` (yours, but not filed in THIS workspace) must not
    // read like `resource_not_accessible` (not yours at all).
    for (const tree of [en, zh]) {
      const errs = at(tree, 'saveAsAsset.err') as Record<string, string>;
      const sentences = RESOURCE_SAVE_AS_ASSET_ERROR_CODES.map((c) => errs[c]);
      expect(new Set(sentences).size).toBe(sentences.length);
      expect(sentences).not.toContain(errs.generic);
    }
  });

  it('the English strings are the UI language, and carry no emoji', () => {
    // CLAUDE.md: UI text is English (Title Case for labels, sentences for
    // messages) and emoji-free.
    const strings = [
      ...MENU_KEYS.map((k) => at(en, k) as string),
      ...RESOURCE_SAVE_AS_ASSET_ERROR_CODES.map((c) => at(en, `saveAsAsset.err.${c}`) as string),
    ];
    for (const value of strings) {
      expect(value).not.toMatch(/[一-鿿]/);
      expect(value).not.toMatch(/\p{Extended_Pictographic}/u);
    }
    // The two labels are Title Case; the refusals are sentences.
    expect(at(en, 'resources.saveAsAsset')).toBe('As Asset');
    expect(at(en, 'resources.saveAsAssetSource')).toBe('My Uploads');
  });

  it('the zh strings are actually translated, not the English copied over', () => {
    for (const key of MENU_KEYS) expect(at(zh, key)).not.toBe(at(en, key));
    for (const code of RESOURCE_SAVE_AS_ASSET_ERROR_CODES) {
      const key = `saveAsAsset.err.${code}`;
      expect(at(zh, key)).not.toBe(at(en, key));
    }
  });
});
