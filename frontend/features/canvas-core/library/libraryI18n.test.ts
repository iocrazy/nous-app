// features/canvas-core/library/libraryI18n.test.ts
//
// en/zh parity for `canvas.library.*`.
//
// Every t() on the canvas passes an English default, so a key missing from
// en.json still renders correctly and reports nothing, and a key missing from
// zh.json falls back to English for zh users only — which nobody on an English
// machine ever sees.
//
// The key list is scanned from the WHOLE canvas-core tree rather than a
// hand-kept file list (which is what `canvasAssetEntryI18n.test.ts` uses and
// what makes that test go stale the day a file is added). Later tasks in this
// plan legitimately use `canvas.library.*` keys from `smart/` and `ui/`, so a
// scan limited to `library/` would leave those unguarded. A new file anywhere
// under canvas-core is covered the moment it exists.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../../public/locales');
const DIR = path.resolve(__dirname, '..');

function sources(dir: string): string[] {
  return fs
    .readdirSync(dir, { withFileTypes: true })
    .flatMap((e) =>
      e.isDirectory()
        ? sources(path.join(dir, e.name))
        : /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name)
          ? [path.join(dir, e.name)]
          : [],
    );
}

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

const PATTERN = /'(canvas\.library\.[A-Za-z0-9_.]+)'/g;

// i18next resolves `foo` to `foo_one` / `foo_other` when a `count` is passed,
// so a plural key is DECLARED in two halves and USED as one. Expanding here is
// what lets the "nothing unused" case below stay exact.
const PLURAL_SUFFIXES = ['_one', '_other'];

const used = [
  ...new Set(
    sources(DIR).flatMap((f) =>
      [...fs.readFileSync(f, 'utf8').matchAll(PATTERN)].map((m) => m[1]),
    ),
  ),
].sort();

const en = load('en');
const zh = load('zh');

function resolved(tree: Record<string, unknown>, key: string): boolean {
  if (typeof at(tree, key) === 'string') return true;
  return PLURAL_SUFFIXES.every((s) => typeof at(tree, `${key}${s}`) === 'string');
}

describe('canvas.library i18n', () => {
  it('found the keys at all — an empty list would pass every case below', () => {
    expect(used.length).toBeGreaterThan(4);
  });

  it.each(used)('%s is translated in both locales', (key) => {
    expect(resolved(en, key), `${key} missing from en.json`).toBe(true);
    expect(resolved(zh, key), `${key} missing from zh.json`).toBe(true);
  });

  it('the zh values are actually translated, not copied English', () => {
    const copied = used.filter((k) => {
      const e = at(en, k) ?? at(en, `${k}_other`);
      const z = at(zh, k) ?? at(zh, `${k}_other`);
      return typeof e === 'string' && e === z;
    });
    expect(copied).toEqual([]);
  });

  it('no locale carries a canvas.library key nothing asks for', () => {
    const declared = Object.keys(
      (at(en, 'canvas.library') ?? {}) as Record<string, unknown>,
    )
      .map((k) => k.replace(/_(one|other)$/, ''))
      .filter((k, i, a) => a.indexOf(k) === i)
      .sort();
    expect(declared).toEqual(used.map((k) => k.replace('canvas.library.', '')).sort());
  });

  it('the two locales carry exactly the same canvas.library keys', () => {
    const keys = (tree: Record<string, unknown>) =>
      Object.keys((at(tree, 'canvas.library') ?? {}) as Record<string, unknown>).sort();
    expect(keys(zh)).toEqual(keys(en));
  });
});
