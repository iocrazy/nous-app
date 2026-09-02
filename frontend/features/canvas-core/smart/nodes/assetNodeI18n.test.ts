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
//
// ⚠️ ONE NAMESPACE (P4 Task 8). The run-report strings — the provider's
// reference ceiling, what the bundle would not send, and why — used to live
// under `assets.node.*` while everything else on the same card lived under
// `canvas.asset.*`. Two namespaces for one component meant a translator
// touching "the asset card" saw half of it, and the split tracked nothing:
// both halves are rendered by `AssetNodeView`, both are canvas copy. They are
// now all `canvas.asset.*`, and the "no key nothing asks for" case below is
// what stops the old family from being resurrected in a locale file alone.

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

/**
 * Every `canvas.asset.*` key the two components ask for by a LITERAL.
 *
 * `dropReason.${reason}` is built at runtime from a backend code and so has no
 * literal to find — it is enumerated from the contract instead, below.
 */
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

/** A key is translated when it is a string, or when BOTH plural forms are. */
function translated(tree: Record<string, unknown>, key: string): boolean {
  if (typeof at(tree, key) === 'string') return true;
  return (
    typeof at(tree, `${key}_one`) === 'string' &&
    typeof at(tree, `${key}_other`) === 'string'
  );
}

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

describe('asset node i18n', () => {
  it('found the keys and the reason codes at all — empty lists would pass every case below', () => {
    expect(usedKeys.length).toBeGreaterThan(10);
    expect(dropReasons.length).toBeGreaterThan(1);
  });

  it.each(usedKeys)('%s is translated in both locales', (key) => {
    expect(translated(en, key), `${key} missing from en.json`).toBe(true);
    expect(translated(zh, key), `${key} missing from zh.json`).toBe(true);
  });

  it('the run-report keys are part of this namespace, not a second one', () => {
    // The unification itself, pinned. Moving `refsLimit` back under
    // `assets.node.*` would leave the card half-translated again, and the
    // source-derived list above would not notice — it only looks at
    // `canvas.asset.*`.
    for (const key of [
      'canvas.asset.refsLimit',
      'canvas.asset.refsDropped',
      'canvas.asset.refsDroppedGroup',
      'canvas.asset.bundleFailed',
    ]) {
      expect(usedKeys, `${key} is not asked for by the node`).toContain(key);
    }
    // And the retired namespace is gone from both locale files entirely.
    expect(at(en, 'assets.node')).toBeUndefined();
    expect(at(zh, 'assets.node')).toBeUndefined();
  });

  it.each(dropReasons)('the %s reason has a label in both locales', (reason) => {
    const key = `canvas.asset.dropReason.${reason}`;
    expect(typeof at(en, key), `${key} missing from en.json`).toBe('string');
    expect(typeof at(zh, key), `${key} missing from zh.json`).toBe('string');
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
    const declared = Object.keys(
      (at(en, 'canvas.asset') ?? {}) as Record<string, unknown>,
    ).filter((k) => k !== 'dropReason');
    const copied = [
      ...declared.map((k) => `canvas.asset.${k}`),
      ...dropReasons.map((r) => `canvas.asset.dropReason.${r}`),
    ].filter((k) => at(en, k) === at(zh, k));
    expect(copied).toEqual([]);
  });

  it('no locale carries a canvas.asset key nothing asks for', () => {
    const declared = [
      ...new Set(
        Object.keys((at(en, 'canvas.asset') ?? {}) as Record<string, unknown>)
          .filter((k) => k !== 'dropReason')
          .map((k) => `canvas.asset.${k.replace(/_(one|other)$/, '')}`),
      ),
    ].sort();
    expect(declared).toEqual(usedKeys);
  });

  it('no locale carries a drop-reason label the contract does not name', () => {
    const declared = Object.keys(
      (at(en, 'canvas.asset.dropReason') ?? {}) as Record<string, unknown>,
    ).sort();
    expect(declared).toEqual(dropReasons);
  });

  it('the two locales carry exactly the same canvas.asset keys', () => {
    const keys = (tree: Record<string, unknown>) =>
      Object.keys((at(tree, 'canvas.asset') ?? {}) as Record<string, unknown>).sort();
    expect(keys(zh)).toEqual(keys(en));
  });
});
