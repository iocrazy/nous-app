/**
 * CharacterLibrary — the character bible card wall (character canvas epic
 * PR-CC4, user-approved design 2026-07-13).
 *
 * Replaces the read-only derived list for `characters`: authored
 * project_characters rows rendered as large bible cards — 3:4 portrait,
 * role badge, editable bio, grouped tag chips, an asset strip (generated
 * media linked by character_id; wired fully in CC5) and "Open in Canvas"
 * which reuses-or-creates the character's kind='character' canvas seeded
 * with the preset agent workflow (CC2).
 *
 * Empty state = "Extract from script": materializes the script-derived
 * entity names into rows (idempotent server-side upsert).
 *
 * Non-empty state used to hide that same path behind one quiet header button,
 * so a project whose scripts already name six characters could sit next to an
 * empty-looking library with nothing connecting the two (user report: "why
 * didn't the script's characters create cards here?"). The library now diffs
 * the script cast against its own rows and says so — see the import hint bar.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Loader2, Plus, Sparkles, UserRound, Wand2 } from 'lucide-react';
import { Loading } from '../common/Loading';

import {
  createCharacter,
  extractCharactersFromScript,
  listCharacters,
  updateCharacter,
  type ProjectCharacter,
} from '../../services/charactersService';
import { fetchProjectEntities } from '../../services/projectsService';
import { missingCharacterNames } from './characterNameMatch';
import {
  createCanvas,
  listCanvases,
} from '../../features/canvas-core/services/canvasService';
import { useToast } from '../Toast';
import { EntityAssetStrip } from './EntityAssetStrip';

interface CharacterLibraryProps {
  projectId: string;
}

const ROLE_TONE: Record<string, string> = {
  lead: 'bg-indigo-500/15 text-indigo-400',
  support: 'bg-emerald-500/15 text-emerald-400',
  antagonist: 'bg-rose-500/15 text-rose-400',
};

/** Canvas name convention — how Open in Canvas finds an existing board. */
const canvasNameFor = (name: string) => `${name} · Character`;

export function CharacterLibrary({ projectId }: CharacterLibraryProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId?: string }>();
  const { addToast } = useToast();
  const [characters, setCharacters] = useState<ProjectCharacter[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // character id or 'extract'/'new'
  /** Script-derived cast names (same source the server's extract reads), so
   *  the hint's count is exactly what a click would materialize. Best-effort:
   *  a failed fetch leaves it empty and simply shows no hint. */
  const [scriptCast, setScriptCast] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    listCharacters(projectId)
      .then((rows) => {
        if (!cancelled) setCharacters(rows);
      })
      .catch((err) => {
        console.error('[CharacterLibrary] load failed:', err);
        if (!cancelled) setCharacters([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const loadScriptCast = useCallback(async () => {
    try {
      const entities = await fetchProjectEntities(projectId);
      setScriptCast((entities.characters ?? []).map((c) => c.name));
    } catch (err) {
      console.error('[CharacterLibrary] script cast load failed:', err);
      setScriptCast([]);
    }
  }, [projectId]);

  useEffect(() => {
    void loadScriptCast();
  }, [loadScriptCast]);

  /** Script names with no card yet — drives the import hint bar. */
  const missingNames = useMemo(
    () => missingCharacterNames(scriptCast, (characters ?? []).map((c) => c.name)),
    [scriptCast, characters],
  );

  const handleExtract = async () => {
    if (busy) return;
    setBusy('extract');
    const before = characters?.length ?? 0;
    try {
      const rows = await extractCharactersFromScript(projectId);
      setCharacters(rows);
      // Typed echo — never a silent no-op (CLAUDE.md). The server upserts with
      // ON CONFLICT DO NOTHING, so the row delta *is* the number imported and
      // nothing the user curated (including a blank "New Character") is touched.
      const imported = Math.max(0, rows.length - before);
      addToast(
        imported > 0
          ? t('characters.importedCount', {
              count: imported,
              defaultValue_one: 'Imported {{count}} character from your scripts',
              defaultValue_other: 'Imported {{count}} characters from your scripts',
            })
          : t(
              'characters.importedNone',
              'Every script character is already in the library',
            ),
        imported > 0 ? 'success' : 'info',
      );
      await loadScriptCast();
    } catch (err) {
      console.error('[CharacterLibrary] extract failed:', err);
      addToast(t('characters.extractFailed', 'Failed to extract characters'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handleCreate = async () => {
    if (busy) return;
    setBusy('new');
    try {
      const row = await createCharacter(projectId, {
        name: t('characters.untitled', 'New Character'),
      });
      setCharacters((prev) => [...(prev ?? []), row]);
    } catch (err) {
      console.error('[CharacterLibrary] create failed:', err);
      addToast(t('characters.createFailed', 'Failed to create character'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handlePatch = async (
    row: ProjectCharacter,
    fields: { name?: string; description?: string },
  ) => {
    // Optimistic local write; server errors roll back via reload-on-toast.
    setCharacters(
      (prev) => prev?.map((c) => (c.id === row.id ? { ...c, ...fields } : c)) ?? prev,
    );
    try {
      await updateCharacter(projectId, row.id, fields);
    } catch (err) {
      console.error('[CharacterLibrary] update failed:', err);
      addToast(t('characters.updateFailed', 'Failed to save character'), 'error');
    }
  };

  const handleOpenCanvas = async (row: ProjectCharacter) => {
    if (busy) return;
    setBusy(row.id);
    try {
      const wanted = canvasNameFor(row.name);
      const existing = (await listCanvases(projectId)).find(
        (c) => c.kind === 'character' && c.name === wanted,
      );
      const canvas =
        existing ??
        (await createCanvas(projectId, { name: wanted, kind: 'character' }));
      const params = new URLSearchParams({
        characterId: row.id,
        name: row.name,
        description: row.description,
      });
      const base = teamId ? `/team/${teamId}/canvas/${canvas.id}` : `/canvas/${canvas.id}`;
      navigate(`${base}?${params.toString()}`);
    } catch (err) {
      console.error('[CharacterLibrary] open canvas failed:', err);
      addToast(t('characters.openCanvasFailed', 'Failed to open character canvas'), 'error');
      setBusy(null);
    }
  };

  if (characters === null) {
    return (
      <div className="flex h-40 items-center justify-center text-ink-500">
        <Loading center />
      </div>
    );
  }

  if (characters.length === 0) {
    return (
      <div
        data-testid="character-library-empty"
        className="flex flex-col items-center justify-center gap-3 py-16 text-center"
      >
        <UserRound size={28} className="text-ink-500" />
        <p className="text-sm text-ink-300">
          {t('characters.emptyTitle', 'No characters yet')}
        </p>
        <p className="max-w-sm text-xs text-ink-500">
          {t(
            'characters.emptyHint',
            'Extract the cast from your scripts, or add one by hand.',
          )}
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleExtract}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
          >
            {busy === 'extract' ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Wand2 size={13} />
            )}
            {t('characters.extract', 'Extract from script')}
          </button>
          <button
            type="button"
            onClick={handleCreate}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
          >
            <Plus size={13} />
            {t('characters.add', 'Add Character')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="character-library">
      {/* Import hint — only while the script names someone the library lacks.
          Empty diff renders nothing, so this never becomes standing noise. */}
      {missingNames.length > 0 && (
        <div
          data-testid="character-import-hint"
          className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-info-line bg-info-soft px-3 py-2"
        >
          <div className="min-w-0">
            <p className="text-xs font-medium text-info">
              {t('characters.missingFromLibrary', {
                count: missingNames.length,
                defaultValue_one:
                  '{{count}} character in your scripts is not in this library yet',
                defaultValue_other:
                  '{{count}} characters in your scripts are not in this library yet',
              })}
            </p>
            <p className="mt-0.5 truncate text-[11px] text-ink-400">
              {missingNames.slice(0, 6).join(' · ')}
              {missingNames.length > 6 ? ' …' : ''}
            </p>
          </div>
          <button
            type="button"
            onClick={handleExtract}
            disabled={busy !== null}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-info-line bg-info-soft px-2.5 py-1 text-xs font-medium text-info hover:bg-info-soft/70 disabled:opacity-60"
          >
            {busy === 'extract' ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Wand2 size={12} />
            )}
            {t('characters.importMissing', 'Import them')}
          </button>
        </div>
      )}

      <div className="mb-3 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={handleExtract}
          disabled={busy !== null}
          className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
        >
          <Wand2 size={12} />
          {t('characters.extract', 'Extract from script')}
        </button>
        <button
          type="button"
          onClick={handleCreate}
          disabled={busy !== null}
          className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
        >
          <Plus size={12} />
          {t('characters.add', 'Add Character')}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {characters.map((row) => (
          <div
            key={row.id}
            data-testid="character-card"
            className="rounded-2xl border border-ink-800 bg-ink-900/60 p-4"
          >
            <div className="flex gap-4">
              {/* 3:4 portrait */}
              <div className="h-44 w-[132px] shrink-0 overflow-hidden rounded-xl border border-ink-800 bg-ink-950/50">
                {row.portrait_url ? (
                  <img
                    src={row.portrait_url}
                    alt={row.name}
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-ink-600">
                    <UserRound size={36} />
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <input
                    className="min-w-0 flex-1 bg-transparent text-base font-semibold text-ink-100 outline-none focus:ring-1 focus:ring-indigo-500/40"
                    defaultValue={row.name}
                    aria-label={t('characters.nameLabel', 'Character name')}
                    onBlur={(e) => {
                      const v = e.target.value.trim();
                      if (v && v !== row.name) void handlePatch(row, { name: v });
                    }}
                  />
                  {row.role_tag && (
                    <span
                      className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
                        ROLE_TONE[row.role_tag] ?? 'bg-ink-800 text-ink-400'
                      }`}
                    >
                      {t(`characters.role.${row.role_tag}`, row.role_tag)}
                    </span>
                  )}
                </div>
                <textarea
                  className="mt-2 h-24 w-full resize-none bg-transparent text-xs leading-relaxed text-ink-300 outline-none placeholder:text-ink-600 focus:ring-1 focus:ring-indigo-500/40"
                  defaultValue={row.description}
                  placeholder={t('characters.bioPlaceholder', 'Backstory, look, temperament…')}
                  aria-label={t('characters.bioLabel', 'Character bio')}
                  onBlur={(e) => {
                    if (e.target.value !== row.description)
                      void handlePatch(row, { description: e.target.value });
                  }}
                />
                {/* Grouped tag chips */}
                <div className="mt-1 flex flex-wrap gap-1">
                  {Object.entries(row.tags ?? {}).flatMap(([group, values]) =>
                    (values ?? []).map((v) => (
                      <span
                        key={`${group}:${v}`}
                        className="rounded-full bg-ink-800/80 px-2 py-0.5 text-[10px] text-ink-400"
                        title={group}
                      >
                        {v}
                      </span>
                    )),
                  )}
                </div>
              </div>
            </div>

            {/* Asset strip — media linked by character_id lands here (CC5). */}
            <div className="mt-3 flex items-center justify-between gap-3 border-t border-ink-800 pt-3">
              <EntityAssetStrip entityKind="character" entityId={row.id} />
              <button
                type="button"
                onClick={() => void handleOpenCanvas(row)}
                disabled={busy !== null}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
              >
                {busy === row.id ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Sparkles size={12} />
                )}
                {t('characters.openCanvas', 'Open in Canvas')}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
