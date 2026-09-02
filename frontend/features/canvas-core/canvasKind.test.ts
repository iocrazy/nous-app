// features/canvas-core/canvasKind.test.ts
//
// `CanvasKind` is a TypeScript union — it has no runtime value, so nothing
// can compare it to anything at test time without reading the source. That is
// what this file does, and it is the same idiom the backend already uses to
// keep `assetSlots.ts` and `slots.py` honest (`test_slots_frontend_mirror.py`
// parses the TS file rather than re-typing its contents).
//
// The authority is the DB CHECK in the migration, not either enum: a value
// missing from the union is a kind the client can never name (which is
// exactly how 'costume' sat unreachable from mig 446 until P4 ruling G),
// while a value in the union but not the CHECK is a 23514 at write time.
//
// The list-vs-membership distinction matters here. `canvases_kind_check` is
// rewritten by DROP + ADD and has been seven times now (280, 357, 358, 362,
// 421, 446 …); each rewrite restates the whole list, so its real failure mode
// is a value silently going missing — which an "is 'costume' in there?"
// assertion cannot see. Both sides are compared as SETS.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { canvasKindFor } from '../../components/resources/assets/sheet/assetSheetModel';
import { ASSET_TYPES } from '../../components/assets/assetSlots';
import { isSmartFamily, type CanvasKind, type CreatableCanvasKind } from './types';

const REPO_ROOT = path.resolve(__dirname, '../..', '..');
const MIGRATIONS = path.join(REPO_ROOT, 'supabase', 'migrations');

/** Every literal `canvases_kind_check` allows, from the latest migration that
 *  rewrites it. Reading the newest one is the point: an older file's list is
 *  a superseded statement, not the current rule. */
function kindsFromMigrations(): Set<string> {
  const files = fs
    .readdirSync(MIGRATIONS)
    .filter((f) => f.endsWith('.sql'))
    .sort();
  let latest: string | null = null;
  for (const file of files) {
    const sql = fs.readFileSync(path.join(MIGRATIONS, file), 'utf8');
    const m = sql.match(/canvases_kind_check[\s\S]*?CHECK\s*\(\s*kind\s+IN\s*\(([^)]*)\)/i);
    if (m) latest = m[1];
  }
  if (latest === null) throw new Error('no migration defines canvases_kind_check');
  return new Set([...latest.matchAll(/'([^']+)'/g)].map((m) => m[1]));
}

/** The members of the `CanvasKind` union, read out of the source file. */
function kindsFromUnion(): Set<string> {
  const src = fs.readFileSync(path.join(__dirname, 'types.ts'), 'utf8');
  const m = src.match(/export type CanvasKind =([\s\S]*?);/);
  if (m === null) throw new Error('CanvasKind union not found in types.ts');
  // Strip `//` comments first: the union carries prose, and an apostrophe in
  // it ("a costume asset's …") reads as a quoted literal otherwise — which
  // fails LOUDLY here but would be an invisible mis-parse in a laxer check.
  const members = m[1].replace(/\/\/[^\n]*/g, '');
  return new Set([...members.matchAll(/'([^']+)'/g)].map((x) => x[1]));
}

describe('CanvasKind mirrors the DB CHECK', () => {
  it('the parser actually found something (a silent empty set would pass)', () => {
    expect(kindsFromMigrations().size).toBeGreaterThan(5);
    expect(kindsFromUnion().size).toBeGreaterThan(5);
  });

  it('the union and canvases_kind_check hold the same set', () => {
    expect([...kindsFromUnion()].sort()).toEqual([...kindsFromMigrations()].sort());
  });

  it('costume is in both', () => {
    expect(kindsFromMigrations().has('costume')).toBe(true);
    expect(kindsFromUnion().has('costume')).toBe(true);
  });
});

describe('canvasKindFor', () => {
  it('every asset type maps to a kind the enum actually has', () => {
    const union = kindsFromUnion();
    for (const type of ASSET_TYPES) {
      expect(union.has(canvasKindFor(type))).toBe(true);
    }
  });

  it('costume gets its own kind instead of downgrading to smart', () => {
    expect(canvasKindFor('costume')).toBe('costume');
  });

  it('the four typed kinds are typed, the other two fall back to smart', () => {
    expect(canvasKindFor('character')).toBe('character');
    expect(canvasKindFor('location')).toBe('location');
    expect(canvasKindFor('prop')).toBe('prop');
    expect(canvasKindFor('prompt')).toBe('smart');
    expect(canvasKindFor('audio')).toBe('smart');
  });

  it('every kind it can return is CREATABLE — the sheet POSTs it', () => {
    // A compile-time assertion as much as a runtime one: `createCanvas` takes
    // `CreatableCanvasKind`, so a kind this function can produce but the
    // create schema refuses would be a 422 from a button that looks fine.
    for (const type of ASSET_TYPES) {
      const kind: CreatableCanvasKind = canvasKindFor(type);
      expect(typeof kind).toBe('string');
    }
  });

  it('every kind it can return renders the smart surface', () => {
    // `CanvasSurface` passes `nodeTypes` only for the smart family. A kind
    // outside it renders every node with React Flow's default renderer —
    // the blank-canvas failure class (2026-08-12). Adding a kind to the enum
    // without adding it here is how that happens quietly.
    for (const type of ASSET_TYPES) {
      expect(isSmartFamily(canvasKindFor(type) as CanvasKind)).toBe(true);
    }
  });
});
