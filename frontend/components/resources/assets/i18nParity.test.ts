// frontend/components/resources/assets/i18nParity.test.ts
//
// en/zh parity for the asset-library namespaces.
//
// The six type labels are addressed by a key BUILT at runtime —
// `assets.types.${type}` in ResourcesSidebar — so no literal for them exists
// anywhere in the source for a grep to find. The sidebar also passes no
// English fallback for them, so a missing key does not read as English: it
// renders the raw key ("assets.types.prop") in the rail. Either way nothing
// reports it, which is why this test reads the locale files directly.
//
// The type list comes from `assetSlots.ts`, so adding a seventh asset type
// fails here until both locales carry its label.

import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

import { ASSET_TYPES } from '../../assets/assetSlots';
import { READINESS_VALUES, SORT_VALUES } from './assetFilters';

const LOCALES = path.resolve(__dirname, '../../../public/locales');

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

describe('Asset library i18n parity', () => {
  it('the sidebar rail entry is translated in both locales', () => {
    expect(typeof at(en, 'resources.assets')).toBe('string');
    expect(typeof at(zh, 'resources.assets')).toBe('string');
  });

  it.each(ASSET_TYPES)('assets.types.%s resolves in both locales', (type) => {
    expect(typeof at(en, `assets.types.${type}`)).toBe('string');
    expect(typeof at(zh, `assets.types.${type}`)).toBe('string');
  });

  it('the two locales carry exactly the same type keys', () => {
    const keys = (tree: Record<string, unknown>) =>
      Object.keys((at(tree, 'assets.types') ?? {}) as Record<string, unknown>).sort();
    expect(keys(en).length).toBeGreaterThan(0);
    expect(keys(zh)).toEqual(keys(en));
    // And no SEVENTH key nobody renders — a stale label is a type the rail
    // stopped showing without anyone noticing it was removed.
    expect(keys(en)).toEqual([...ASSET_TYPES].sort());
  });

  it('the English rail labels are Title Case plurals', () => {
    // UI text is English, Title Case (CLAUDE.md). The plural matters: these
    // name SHELVES ("Characters"), not one asset — `saveAsAsset.type.*` is the
    // singular set and is a different namespace on purpose.
    const singular: Record<string, string> = {
      character: 'Characters',
      location: 'Locations',
      prop: 'Props',
      costume: 'Costumes',
      prompt: 'Prompts',
      audio: 'Audio',
    };
    for (const type of ASSET_TYPES) {
      expect(at(en, `assets.types.${type}`), type).toBe(singular[type]);
    }
    expect(at(en, 'resources.assets')).toBe('Assets');
  });

  // ── The shelf's own copy (Task 6) ──
  //
  // Everything `AssetShelf` / `AssetCard` / `NewAssetDialog` addresses under
  // `assets.*`. Several of these keys are built at runtime and therefore have
  // no literal in the source for a grep to find:
  //   `assets.types.${type}`      (tab bar + rail)
  //   `assets.readiness.${state}` (filter chip + card chip)
  //   `assets.sort.${value}`      (sort chip)
  //   `assets.err.${code}`        (whatever the backend refuses with)
  // The runtime-built ones are enumerated from the same vocabularies the code
  // iterates, so adding a sort order or a readiness value fails here until
  // both locales carry its label.
  const SHELF_KEYS = [
    'assets.tab.all',
    'assets.new',
    'assets.newOfType',
    'assets.loadMore',
    'assets.loadFailed',
    'assets.projectsUnavailable',
    'assets.importFromScript',
    'assets.importFromScriptHint',
    'assets.importFromScriptGo',
    'assets.filter.project',
    'assets.filter.anyProject',
    'assets.filter.readiness',
    'assets.filter.anyReadiness',
    'assets.filter.tag',
    'assets.filter.anyTag',
    'assets.filter.noTags',
    'assets.filter.sort',
    'assets.card.open',
    'assets.card.missing',
    'assets.card.coverage',
    'assets.card.moreProjects',
    'assets.presets.title',
    'assets.presets.hint',
    'assets.empty.none',
    'assets.empty.ofType',
    'assets.empty.filtered',
    'assets.dialog.title',
    'assets.dialog.name',
    'assets.dialog.role',
    'assets.dialog.description',
    'assets.dialog.create',
    'assets.dialog.nameRequired',
    'assets.dialog.openExisting',
    // Backend codes the two shelves can actually surface.
    'assets.err.generic',
    'assets.err.network',
    'assets.err.not_a_member',
    'assets.err.not_found',
    'assets.err.asset_exists',
    'assets.err.system_preset_readonly',
    'assets.err.unreadable_body',
    ...READINESS_VALUES.map((v) => `assets.readiness.${v}`),
    ...SORT_VALUES.map((v) => `assets.sort.${v}`),
  ];

  it.each(SHELF_KEYS)('%s resolves in both locales', (key) => {
    expect(typeof at(en, key), `en ${key}`).toBe('string');
    expect(typeof at(zh, key), `zh ${key}`).toBe('string');
  });

  it('interpolation placeholders survive translation', () => {
    // A zh string that dropped `{{name}}` renders "Open " with nothing after
    // it — a bug no key-existence check can see.
    const placeholders: Record<string, string[]> = {
      'assets.newOfType': ['type'],
      'assets.card.open': ['name'],
      'assets.card.missing': ['slots'],
      'assets.card.coverage': ['filled', 'total'],
      'assets.card.moreProjects': ['n'],
      'assets.empty.ofType': ['type'],
      'assets.dialog.title': ['type'],
    };
    for (const [key, vars] of Object.entries(placeholders)) {
      for (const tree of [en, zh]) {
        for (const name of vars) {
          expect(String(at(tree, key)), `${key} / ${name}`).toContain(`{{${name}}}`);
        }
      }
    }
  });

  it('the shelf copy is actually translated, not copied across', () => {
    // Same rule as the type labels below. Exemptions are strings that are
    // legitimately identical in both: bare placeholders and Latin words we do
    // not localize.
    const exempt = new Set(['assets.card.moreProjects']);
    const copied = SHELF_KEYS.filter(
      (key) => !exempt.has(key) && at(zh, key) === at(en, key),
    );
    expect(copied).toEqual([]);
  });

  it('no zh label was left as its English source', () => {
    // A copy-paste of the English string is a silently untranslated key: it
    // reads as "done" to every check that only asks whether the key exists.
    // `Audio` is legitimately identical in both, so it is the one exemption.
    const untranslated = ASSET_TYPES.filter(
      (type) =>
        type !== 'audio' &&
        at(zh, `assets.types.${type}`) === at(en, `assets.types.${type}`),
    );
    expect(untranslated).toEqual([]);
    expect(at(zh, 'resources.assets')).not.toBe(at(en, 'resources.assets'));
  });
});
