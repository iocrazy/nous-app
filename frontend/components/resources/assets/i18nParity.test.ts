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

import { ASSET_TYPES, LINK_RELATIONS } from '../../assets/assetSlots';
import { ASSET_SOURCES } from '../../../services/assetsService';
import { RELATION_SECTION_KEYS } from './sheet/assetSheetModel';
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

  // ── The entity sheet's copy (Task 7) ──
  //
  // Same reasoning as the shelf block, with three more runtime-built families:
  //   `assets.rel.${spec.key}`         (section headings)
  //   `assets.rel.add.${relation}`     (link dialog titles)
  //   `assets.source.${row.source}`    (Details panel)
  //   `assets.sheet.lang.${lang}`      (EN/ZH toggle)
  // Each is enumerated from the vocabulary the code iterates, so adding a
  // relation or an asset source fails here until both locales carry its label.
  const SHEET_KEYS = [
    'assets.sheet.board',
    'assets.sheet.arrange',
    'assets.sheet.arrangeHint',
    'assets.sheet.reorder',
    'assets.sheet.equip',
    'assets.sheet.generate',
    'assets.sheet.comingSoon',
    'assets.sheet.loadouts',
    'assets.sheet.loadoutName',
    'assets.sheet.newLoadout',
    'assets.sheet.renameLoadout',
    'assets.sheet.deleteLoadout',
    'assets.sheet.setDefaultLoadout',
    'assets.sheet.defaultLoadout',
    'assets.sheet.activeLoadout',
    'assets.sheet.prompt',
    'assets.sheet.positive',
    'assets.sheet.negative',
    'assets.sheet.translate',
    'assets.sheet.regenerate',
    'assets.sheet.placeholders',
    'assets.sheet.platformParams',
    'assets.sheet.paramName',
    'assets.sheet.paramValue',
    'assets.sheet.addParam',
    'assets.sheet.removeParam',
    'assets.sheet.noParams',
    'assets.sheet.noExamples',
    'assets.sheet.noAudio',
    'assets.sheet.noVariants',
    'assets.sheet.loopable',
    'assets.sheet.notLoopable',
    'assets.sheet.sendToCanvas',
    'assets.sheet.sendToAgent',
    'assets.sheet.arrivesWithP4',
    'assets.sheet.arrivesWithP5',
    'assets.sheet.copyLoadoutPrompt',
    'assets.sheet.promptCopied',
    'assets.sheet.copyFailed',
    'assets.sheet.nothingToCopy',
    'assets.sheet.duplicate',
    'assets.sheet.delete',
    'assets.sheet.confirmDelete',
    'assets.sheet.openInCanvas',
    'assets.sheet.pickProject',
    'assets.sheet.noProjectsToPick',
    'assets.sheet.details',
    'assets.sheet.type',
    'assets.sheet.subtype',
    'assets.sheet.source',
    'assets.sheet.updated',
    'assets.sheet.assetId',
    'assets.sheet.usedIn',
    'assets.sheet.noProjects',
    'assets.sheet.canvasUsageLater',
    'assets.sheet.addRole',
    'assets.sheet.setting',
    'assets.sheet.addSetting',
    'assets.sheet.addDescription',
    'assets.sheet.editField',
    'assets.sheet.backToAssets',
    'assets.rel.addAction',
    'assets.rel.empty',
    'assets.rel.remove',
    'assets.rel.search',
    'assets.rel.noCandidates',
    'assets.rel.alreadyLinked',
    'assets.rel.blocked.audioSubtype',
    'assets.setting.exterior',
    'assets.setting.interior',
    // Typed refusals the sheet's own actions can produce, straight from
    // `assets_service.py`. A code with no string renders the generic line and
    // the user never learns which rule they hit.
    'assets.err.asset_not_found',
    'assets.err.invalid_slot',
    'assets.err.link_not_allowed',
    'assets.err.link_not_found',
    'assets.err.resource_not_found',
    'assets.err.cannot_delete_default',
    'assets.err.loadout_not_found',
    'assets.err.loadouts_character_only',
    'assets.err.loadout_not_subset',
    'assets.err.field_not_nullable',
    'assets.err.nothing_to_translate',
    'assets.err.translate_unavailable',
    'assets.err.caption_unavailable',
    'assets.err.caption_paused',
    'assets.err.no_primary_file',
    'assets.err.no_image_file',
    'assets.err.file_not_captionable',
    'assets.err.not_applicable',
    'assets.err.project_not_found',
    // Task 8's two dialogs and the generation-history panel.
    'assets.equip.title',
    'assets.equip.target',
    'assets.equip.targetWithLoadout',
    'assets.equip.search',
    'assets.equip.noResults',
    'assets.equip.searchFailed',
    'assets.equip.alreadyAttached',
    'assets.equip.otherWorkspace',
    'assets.equip.selected',
    'assets.equip.attach',
    'assets.equip.attached',
    'assets.equip.nothingAttached',
    'assets.equip.everyLoadout',
    'assets.equip.anotherLoadout',
    'assets.equip.inPlacement',
    'assets.equip.willMove',
    'assets.equip.stillSelected',
    'assets.equip.movedOnly',
    'assets.equip.attachedAndMoved',
    'assets.gen.title',
    'assets.gen.positive',
    'assets.gen.negative',
    'assets.gen.negativeNote',
    'assets.gen.aspect',
    'assets.gen.references',
    'assets.gen.model',
    'assets.gen.modelDefault',
    'assets.gen.count',
    'assets.gen.run',
    'assets.gen.done',
    'assets.gen.produced',
    'assets.gen.unitFailed',
    'assets.gen.skippedRef',
    'assets.gen.openInbox',
    'assets.gen.attachNow',
    'assets.gen.attached',
    'assets.gen.attachFailed',
    'assets.gen.attachOutcome',
    'assets.gen.nothingGenerated',
    'assets.history.title',
    'assets.history.empty',
    'assets.history.unavailable',
    'assets.history.openInbox',
    // Typed refusals only the slot-generation path can produce.
    'assets.err.slot_not_generatable',
    'assets.err.loadout_mismatch',
    'assets.err.generation_failed',
    'assets.err.register_failed',
    ...RELATION_SECTION_KEYS.map((k) => `assets.rel.${k}`),
    ...LINK_RELATIONS.map((r) => `assets.rel.add.${r}`),
    ...ASSET_SOURCES.map((s) => `assets.source.${s}`),
    ...(['en', 'zh'] as const).map((l) => `assets.sheet.lang.${l}`),
  ];

  it.each(SHEET_KEYS)('%s resolves in both locales', (key) => {
    expect(typeof at(en, key), `en ${key}`).toBe('string');
    expect(typeof at(zh, key), `zh ${key}`).toBe('string');
  });

  it('the sheet interpolation placeholders survive translation', () => {
    const placeholders: Record<string, string[]> = {
      'assets.sheet.reorder': ['slot'],
      'assets.sheet.confirmDelete': ['name'],
      'assets.sheet.editField': ['field'],
      'assets.rel.remove': ['name'],
      'assets.rel.add.wears': ['type'],
      'assets.rel.add.holds': ['type'],
      'assets.rel.add.ambience_of': ['type'],
      'assets.rel.add.voice_of': ['type'],
      // Task 8. `{{n}}` rather than `{{count}}` throughout: i18next reads a
      // `count` option as a PLURAL SELECTOR and would look for `_one` /
      // `_other` variants of these keys, none of which exist — the lookup
      // would fall through to the raw key with nothing failing anywhere.
      'assets.equip.title': ['slot'],
      'assets.equip.target': ['slot'],
      'assets.equip.targetWithLoadout': ['slot', 'loadout'],
      'assets.equip.selected': ['n'],
      'assets.equip.attached': ['n', 'slot'],
      'assets.gen.title': ['slot'],
      'assets.gen.references': ['n'],
      'assets.gen.done': ['n'],
      'assets.gen.produced': ['n'],
      'assets.gen.unitFailed': ['n'],
      'assets.gen.skippedRef': ['id'],
      'assets.gen.attached': ['n', 'slot'],
      'assets.gen.attachFailed': ['n'],
      'assets.gen.attachOutcome': ['ok', 'bad'],
      'assets.equip.inPlacement': ['placement'],
      'assets.equip.willMove': ['name', 'from', 'to'],
      'assets.equip.movedOnly': ['m', 'slot'],
      'assets.equip.attachedAndMoved': ['n', 'm', 'slot'],
    };
    for (const [key, vars] of Object.entries(placeholders)) {
      for (const tree of [en, zh]) {
        for (const name of vars) {
          expect(String(at(tree, key)), `${key} / ${name}`).toContain(`{{${name}}}`);
        }
      }
    }
  });

  it('no Task 8 key is a plural family in disguise', () => {
    // The other half of the `{{count}}` trap: a key whose value interpolates
    // `{{count}}` needs `_one`/`_other` siblings to resolve, and adding one
    // later without them re-opens exactly the hole above.
    const bad: string[] = [];
    for (const key of SHEET_KEYS) {
      if (!/^assets\.(equip|gen|history)\./.test(key)) continue;
      for (const tree of [en, zh]) {
        if (String(at(tree, key)).includes('{{count}}')) bad.push(key);
      }
    }
    expect(bad).toEqual([]);
  });

  it('the sheet copy is actually translated, not copied across', () => {
    // The four link-dialog titles are the same English sentence by design
    // (they differ only by the interpolated type), so a zh/en comparison of
    // one covers all four; they are not exempt.
    const copied = SHEET_KEYS.filter((key) => at(zh, key) === at(en, key));
    expect(copied).toEqual([]);
  });

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
