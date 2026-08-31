// frontend/services/rlsTripwire.test.ts
//
// The six asset-library tables are service-role-only (spec §7.0: "六张表
// RLS：service_role-only，前端零直读"). This file is the tripwire that keeps
// them that way.
//
// Why a grep test and not a runtime one: under RLS a forbidden SELECT from the
// browser's anon client does NOT error. PostgREST answers 200 with `[]`. So a
// direct read added in P3–P6 would render as "you have no assets" — a silent,
// self-consistent lie with no console entry, no toast and no failing request.
// That is the recorded `reference-skill-invisible-under-rls-to-its-own-owner`
// shape, and the only cheap way to catch it is before it ships.
//
// The rule this enforces: every read of these tables goes through the backend
// (`services/assetsService.ts` → `/api/v1/assets/*`), which uses the service
// role and gates on scope. `supabase.from(...)` on any of them is a defect.
//
// FAIL-CLOSED. A scan that walks nothing must FAIL, not pass — "we found no
// violations" and "we looked at nothing" are the same output otherwise, which
// is the `reference-empty-output-is-not-a-negative-result` shape. The floor
// and the positive control below are what separate them.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

/** The six tables migration 445/446 put behind service-role-only RLS. */
const GUARDED_TABLES = [
  'assets',
  'asset_files',
  'asset_links',
  'asset_loadouts',
  'asset_project_refs',
  'canvas_asset_refs',
] as const;

const FRONTEND_ROOT = path.resolve(__dirname, '..');

const SKIP_DIRS = new Set(['node_modules', 'dist', 'dist-ssr', 'coverage', '.vite', 'build']);

/**
 * `.from('assets')` and its quote variants, anchored on the full table name so
 * `asset_files` cannot be read as a hit for `assets`. `\s*` because prettier
 * may wrap a long chain.
 */
function readPattern(table: string): RegExp {
  return new RegExp(String.raw`\.from\(\s*['"\`]${table}['"\`]`);
}

/** Every `.ts` / `.tsx` under `frontend/`, excluding this file. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name.startsWith('.') || SKIP_DIRS.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!/\.tsx?$/.test(entry.name)) continue;
      // This file names all six tables in its own source; scanning it would
      // report itself. It is the only exclusion, and it is by exact path.
      if (full === __filename) continue;
      out.push(full);
    }
  };
  walk(FRONTEND_ROOT);
  return out;
}

const FILES = sourceFiles();

describe('asset-library RLS tripwire', () => {
  it('actually walked the frontend tree', () => {
    // The fail-closed half. An empty (or tiny) file list would make every
    // assertion below vacuously true.
    expect(FILES.length).toBeGreaterThan(500);
    // Positive control: the file that legitimately owns asset access must be
    // in the scan. If the walker ever stops reaching `services/`, the table
    // assertions stop meaning anything and this line says so.
    expect(FILES).toContain(path.join(FRONTEND_ROOT, 'services', 'assetsService.ts'));
  });

  it.each(GUARDED_TABLES)('no frontend file reads `%s` through supabase', (table) => {
    const pattern = readPattern(table);
    const offenders = FILES.filter((file) =>
      pattern.test(fs.readFileSync(file, 'utf8')),
    ).map((file) => path.relative(FRONTEND_ROOT, file));

    expect(
      offenders,
      `\`${table}\` is service-role-only. Under RLS the browser's anon client ` +
        `gets 200 + [] rather than an error, so a direct read here renders as ` +
        `"nothing here" with no failure anywhere. Go through the backend ` +
        `(\`services/assetsService.ts\`) instead.`,
    ).toEqual([]);
  });

  it('the pattern would notice a violation', () => {
    // A guard on the guard: a regex that matched nothing would let every
    // assertion above pass on a tree full of direct reads.
    for (const table of GUARDED_TABLES) {
      const pattern = readPattern(table);
      expect(pattern.test(`supabase.from('${table}').select('*')`), table).toBe(true);
      expect(pattern.test(`supabase.from("${table}").select("*")`), table).toBe(true);
    }
    // …and would NOT confuse one table for another whose name it prefixes.
    expect(readPattern('assets').test(`supabase.from('asset_files')`)).toBe(false);
    expect(readPattern('asset_links').test(`supabase.from('asset_link')`)).toBe(false);
  });
});
