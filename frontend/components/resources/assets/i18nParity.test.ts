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
import { LIBRARY_VALUES, READINESS_VALUES, SORT_VALUES } from './assetFilters';

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

  it('the rail chevron has its OWN name, distinct from the entry', () => {
    // Two buttons with the same accessible name in one group is ambiguous to a
    // screen reader and forces every name-based locator into a positional
    // `.first()`. A missing zh value here would silently restore the collision
    // for zh users only.
    expect(typeof at(en, 'resources.assetsExpand')).toBe('string');
    expect(typeof at(zh, 'resources.assetsExpand')).toBe('string');
    expect(at(en, 'resources.assetsExpand')).not.toBe(at(en, 'resources.assets'));
    expect(at(zh, 'resources.assetsExpand')).not.toBe(at(zh, 'resources.assets'));
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
    // The hint under a disabled Load More. `sort=readiness` orders drafts-first
    // per PAGE, so a second page would mis-order the shelf; without this line
    // the button reads as broken rather than deliberately off.
    'assets.readinessSortPaged',
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
    'assets.filter.library',
    'assets.card.open',
    'assets.card.missing',
    'assets.card.coverage',
    'assets.card.moreProjects',
    'assets.presets.title',
    'assets.presets.hint',
    'assets.empty.none',
    'assets.empty.ofType',
    'assets.empty.filtered',
    'assets.empty.nothingOutOfLibrary',
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
    // Library membership (mig 449) — the shelf chip, the card badge, and the
    // add/remove action shared by the project panel and the entity sheet.
    // `assets.library.${value}` is built at runtime from LIBRARY_VALUES, so
    // adding a fourth value fails here until both locales carry its label.
    'assets.library.notInLibrary',
    'assets.library.notInLibraryHint',
    'assets.library.addToLibrary',
    'assets.library.added',
    'assets.library.removed',
    'assets.library.addHint',
    'assets.library.removeHint',
    'assets.library.addNamed',
    'assets.library.removeNamed',
    ...READINESS_VALUES.map((v) => `assets.readiness.${v}`),
    ...SORT_VALUES.map((v) => `assets.sort.${v}`),
    ...LIBRARY_VALUES.map((v) => `assets.library.${v}`),
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
    // `arrivesWithP4` is gone: Send To Canvas shipped in P4 Task 6, so the
    // button no longer wears a "coming later" title and the string has no
    // renderer. `arrivesWithP5` stays — Send To Agent is still a placeholder.
    'assets.sheet.arrivesWithP5',
    // Send To Canvas's own dialog (P4 Task 6). Every failure reason has its
    // own line: a read-only canvas, a lost optimistic-lock race and a dead
    // request are three different things to do next.
    'assets.sendToCanvas.title',
    'assets.sendToCanvas.newCanvas',
    'assets.sendToCanvas.noCanvases',
    'assets.sendToCanvas.canvasesUnavailable',
    'assets.sendToCanvas.err.loadFailed',
    'assets.sendToCanvas.err.readOnly',
    'assets.sendToCanvas.err.conflict',
    'assets.sendToCanvas.err.saveFailed',
    'assets.sendToCanvas.err.createFailed',
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
    // `Attach Now` calls `save-as-asset`, so the codes GeneratedInboxService
    // raises reach the sheet's `t('assets.err.' + code)` too. They were
    // missing until Task 9 and rendered the generic line — a typed refusal
    // read as "something went wrong", which is exactly the failure mode
    // `useAssetFailure` exists to prevent.
    //
    // The other four codes the ASSETS SERVICE can raise —
    // `file_not_attached` (detachFile), `project_scope_mismatch` /
    // `project_ref_not_found` (link/unlinkProject) and `personal_team_missing`
    // (listProjectAssets) — were deliberately NOT listed in P2: those four
    // service functions had zero component call sites then, so a string for
    // them would have been copy no shipped path could render. They belong with
    // the UI that first calls them — and THREE OF THE FOUR now have one. The
    // project workspace panels (P3 Task 5) call link/unlink and the
    // project-scoped read, so `project_scope_mismatch`,
    // `project_ref_not_found` and `personal_team_missing` have moved into
    // `PROJECT_KEYS` below. Of the service's four, only `file_not_attached` is
    // still waiting: `detachFile` remains uncalled.
    //
    // ⚠️ CORRECTED (final review I-1). The sentence above used to end "Only
    // `file_not_attached` is still waiting" full stop, which read as an
    // inventory of every code the ASSETS BACKEND can produce — and it is not.
    // It counts only what `AssetsService` raises. The ROUTER contributes two
    // more of its own: `_project_gate` translates the shared project guard's
    // HTTPException into `project_forbidden` (403) / `project_not_found` (404)
    // with `project_access_denied` as its default, and those never appear in
    // any service. They are in `PROJECT_KEYS` below now. A note that says
    // "coverage is complete" is exactly what stops the next person looking,
    // so the scope of the count is now stated instead of implied.
    'assets.err.not_authorised',
    'assets.err.generation_not_found',
    'assets.err.file_missing',
    // The breadcrumb landmark's accessible name: icon-and-link only, so this
    // string is the entire name a screen reader announces for that nav.
    'assets.sheet.breadcrumb',
    ...RELATION_SECTION_KEYS.map((k) => `assets.rel.${k}`),
    ...LINK_RELATIONS.map((r) => `assets.rel.add.${r}`),
    ...ASSET_SOURCES.map((s) => `assets.source.${s}`),
    ...(['en', 'zh'] as const).map((l) => `assets.sheet.lang.${l}`),
  ];

  // ── The project workspace's panels (P3 Task 5) ──
  //
  // `ProjectAssetsPanel` / `LinkFromLibraryDialog` render the project's view
  // over the library. Three of the `assets.err.*` codes below are the ones the
  // Task-7 block above deliberately left out ("they belong with the UI that
  // first calls them") — link/unlink and the project-scoped read are that UI,
  // so they move here rather than staying unlisted.
  const PROJECT_KEYS = [
    'assets.project.linkFromLibrary',
    'assets.project.linkTitle',
    'assets.project.search',
    'assets.project.noCandidates',
    'assets.project.allLinked',
    'assets.project.searchFailed',
    'assets.project.count',
    'assets.project.empty',
    'assets.project.loadFailed',
    'assets.project.linked',
    'assets.project.unlink',
    'assets.project.unlinkHint',
    'assets.project.unlinked',
    'assets.project.importDone',
    'assets.project.importSkipped',
    'assets.project.importTypeCount',
    'assets.project.importNothing',
    'assets.project.scopeUnknown',
    // The write half of the project refs, now that a component calls it.
    'assets.err.project_scope_mismatch',
    'assets.err.project_ref_not_found',
    'assets.err.personal_team_missing',
    // ROUTER-level, not service-level: `_project_gate` (assets_router) turns
    // the shared project guard's HTTPException into these. P3 is the first
    // release where they are reachable — all four `/projects/{id}/assets*`
    // routes carry that gate and none of them had a caller before this UI.
    //
    // `project_forbidden` is the reachable one, and its scenario is ordinary:
    // a read-only collaborator opens the workspace Characters page (the read
    // passes — `can_read` is granted to any project viewer) and clicks Import
    // From Script, which is deliberately NOT gated on `writesDisabled`
    // because the endpoint resolves its own scope. `verify_project_write_access`
    // answers 403. Without this string the toast was the generic "Something
    // went wrong. Please try again." — a typed refusal rendered as noise,
    // which is the whole failure mode `useAssetFailure` exists to prevent.
    //
    // `project_access_denied` is `_PROJECT_GUARD_CODES`'s DEFAULT arm: it
    // fires only if the guard ever raises a status other than 403/404. No
    // path produces it today. It is listed anyway because the cost of a
    // string is nothing and the cost of the fallback is a user reading
    // "Something went wrong" about a permission decision.
    'assets.err.project_forbidden',
    'assets.err.project_access_denied',
    // Per-item codes only `import-from-script` produces. These reach the user
    // through `t('assets.err.' + item.code)` on the import report's failure
    // lines — a code with no string falls back to the server's English
    // `detail`, which is honest but untranslated.
    'assets.err.empty_name',
    'assets.err.name_too_long',
    'assets.err.already_linked',
    'assets.err.internal_error',
  ];

  it.each(PROJECT_KEYS)('%s resolves in both locales', (key) => {
    expect(typeof at(en, key), `en ${key}`).toBe('string');
    expect(typeof at(zh, key), `zh ${key}`).toBe('string');
  });

  it('the project panel interpolation placeholders survive translation', () => {
    const placeholders: Record<string, string[]> = {
      'assets.project.linkTitle': ['type'],
      'assets.project.count': ['n'],
      'assets.project.empty': ['type'],
      'assets.project.unlink': ['name'],
      'assets.project.importDone': ['created', 'linked', 'skipped'],
      // The import run spans two asset types in one call, so both the summary
      // breakdown and every failure line name the type — a bare count made a
      // Characters panel that gained 3 cards after "5 Created" read as an
      // import that under-delivered.
      'assets.project.importSkipped': ['name', 'type', 'reason'],
      'assets.project.importTypeCount': ['type', 'n'],
    };
    for (const [key, vars] of Object.entries(placeholders)) {
      for (const tree of [en, zh]) {
        for (const name of vars) {
          expect(String(at(tree, key)), `${key} / ${name}`).toContain(`{{${name}}}`);
        }
      }
    }
  });

  it('no project panel key is a plural family in disguise', () => {
    // Same `{{count}}` trap as Task 8's block: `assets.project.count` uses
    // `{{n}}` precisely so i18next does not read it as a plural selector and
    // go looking for `_one` / `_other` siblings that do not exist.
    const bad = PROJECT_KEYS.filter((key) =>
      [en, zh].some((tree) => String(at(tree, key)).includes('{{count}}')),
    );
    expect(bad).toEqual([]);
  });

  it('the project panel copy is actually translated, not copied across', () => {
    const copied = PROJECT_KEYS.filter((key) => at(zh, key) === at(en, key));
    expect(copied).toEqual([]);
  });

  it('the workspace sidebar has a Costumes label in both locales', () => {
    // The four ASSETS modules are one panel over `assets` filtered by type;
    // Costumes is the type that never had a project-local table and so never
    // had a rail entry until P3.
    for (const key of ['characters', 'locations', 'props', 'costumes']) {
      expect(typeof at(en, `projects.workspace.modules.${key}`), key).toBe('string');
      expect(typeof at(zh, `projects.workspace.modules.${key}`), key).toBe('string');
    }
    expect(at(en, 'projects.workspace.modules.costumes')).toBe('Costumes');
    expect(at(zh, 'projects.workspace.modules.costumes')).not.toBe('Costumes');
  });

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
