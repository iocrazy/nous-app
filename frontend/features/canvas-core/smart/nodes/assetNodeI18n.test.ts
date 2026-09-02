// features/canvas-core/smart/nodes/assetNodeI18n.test.ts
//
// en/zh parity for the asset smart node's namespace.
//
// Every `t()` call in the node and its picker passes an English default, so a
// key missing from en.json renders correctly and reports nothing — and a key
// missing from zh.json falls back to English for zh users only, which nobody
// on an English machine ever sees. Reading both locale files is the only way
// this shows up before a user does.
//
// The key list is read out of the SOURCE, not typed out here: a hand-kept
// inventory drifts the moment a string is added, and the drift is silent in
// exactly the direction this test exists to catch.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const LOCALES = path.resolve(__dirname, '../../../../public/locales');
const SOURCES = [
  path.join(__dirname, 'AssetNodeView.tsx'),
  path.join(__dirname, 'AssetPickerDialog.tsx'),
];

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

/** Every `canvas.asset.*` key the two components ask for. */
const usedKeys = [
  ...new Set(
    SOURCES.flatMap((file) => {
      const src = fs.readFileSync(file, 'utf8');
      return [...src.matchAll(/'(canvas\.asset\.[A-Za-z0-9_.]+)'/g)].map((m) => m[1]);
    }),
  ),
].sort();

const en = load('en');
const zh = load('zh');

describe('asset node i18n', () => {
  it('found the keys at all — an empty list would pass every case below', () => {
    expect(usedKeys.length).toBeGreaterThan(10);
  });

  it.each(usedKeys)('%s is translated in both locales', (key) => {
    expect(typeof at(en, key)).toBe('string');
    expect(typeof at(zh, key)).toBe('string');
  });

  it('the drag-create entry is named and described in both locales', () => {
    for (const key of ['canvas.dragCreate.node.asset', 'canvas.dragCreate.desc.asset']) {
      expect(typeof at(en, key)).toBe('string');
      expect(typeof at(zh, key)).toBe('string');
    }
  });

  it('the zh values are actually translated, not copied English', () => {
    // A copied English value passes a "the key exists" check while leaving zh
    // users on English — the failure this file is really about.
    const copied = usedKeys.filter((k) => at(en, k) === at(zh, k));
    expect(copied).toEqual([]);
  });

  it('no locale carries a canvas.asset key nothing asks for', () => {
    const declared = Object.keys(
      (at(en, 'canvas.asset') ?? {}) as Record<string, unknown>,
    ).map((k) => `canvas.asset.${k}`);
    expect(declared.sort()).toEqual(usedKeys);
  });
});

// ── The run-report namespace (`assets.node.*`, P4 Task 5) ──────────────────
// Separate namespace, separate block: these are the strings the node shows
// ABOUT A RUN (the provider's reference ceiling, and what the bundle would not
// send), and the plan names them `assets.node.*`.

/** A key is translated when it is a string, or when BOTH plural forms are. */
function translated(tree: Record<string, unknown>, key: string): boolean {
  if (typeof at(tree, key) === 'string') return true;
  return (
    typeof at(tree, `${key}_one`) === 'string' &&
    typeof at(tree, `${key}_other`) === 'string'
  );
}

const nodeKeys = [
  ...new Set(
    [...fs.readFileSync(SOURCES[0], 'utf8').matchAll(/'(assets\.node\.[A-Za-z0-9_.]+)'/g)].map(
      (m) => m[1],
    ),
  ),
].sort();

/**
 * The drop-reason codes, read out of the BACKEND CONTRACT rather than listed
 * here. `DroppedReason` in `assetsService.ts` mirrors what the bundle endpoint
 * answers; a new reason added there with no label would otherwise render as its
 * raw code — which the node does on purpose (better than omitting a reference),
 * but which nobody would notice until a user saw `provider_no_refs` on screen.
 */
const dropReasons = (() => {
  const src = fs.readFileSync(
    path.resolve(__dirname, '../../../../services/assetsService.ts'),
    'utf8',
  );
  const union = /export type DroppedReason =([^;]+);/.exec(src)?.[1] ?? '';
  return [...union.matchAll(/'([a-z_]+)'/g)].map((m) => m[1]).sort();
})();

describe('asset node run-report i18n', () => {
  it('found the keys and the reason codes at all', () => {
    expect(nodeKeys.length).toBeGreaterThan(2);
    expect(dropReasons.length).toBeGreaterThan(1);
  });

  it.each(nodeKeys)('%s is translated in both locales', (key) => {
    expect(translated(en, key), `${key} missing from en.json`).toBe(true);
    expect(translated(zh, key), `${key} missing from zh.json`).toBe(true);
  });

  it.each(dropReasons)('the %s reason has a label in both locales', (reason) => {
    const key = `assets.node.dropReason.${reason}`;
    expect(typeof at(en, key), `${key} missing from en.json`).toBe('string');
    expect(typeof at(zh, key), `${key} missing from zh.json`).toBe('string');
  });

  it('the zh values are actually translated, not copied English', () => {
    const declared = Object.keys(
      (at(en, 'assets.node') ?? {}) as Record<string, unknown>,
    ).filter((k) => k !== 'dropReason');
    const copied = [
      ...declared.map((k) => `assets.node.${k}`),
      ...dropReasons.map((r) => `assets.node.dropReason.${r}`),
    ].filter((k) => at(en, k) === at(zh, k));
    expect(copied).toEqual([]);
  });

  it('no locale carries an assets.node key nothing asks for', () => {
    const declared = [
      ...new Set(
        Object.keys((at(en, 'assets.node') ?? {}) as Record<string, unknown>)
          .filter((k) => k !== 'dropReason')
          .map((k) => `assets.node.${k.replace(/_(one|other)$/, '')}`),
      ),
    ].sort();
    expect(declared).toEqual(nodeKeys);
  });

  it('no locale carries a drop-reason label the contract does not name', () => {
    const declared = Object.keys(
      (at(en, 'assets.node.dropReason') ?? {}) as Record<string, unknown>,
    ).sort();
    expect(declared).toEqual(dropReasons);
  });
});
