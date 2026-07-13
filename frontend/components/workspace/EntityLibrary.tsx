/**
 * EntityLibrary — the location/prop bible-card wall (SP3), the generalized
 * sibling of CharacterLibrary. One component, entityType-driven: labels,
 * cover aspect, extract availability (locations derive from scene headers;
 * props are manual-only) and the Open in Canvas naming/seeding.
 */

import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Loader2, MapPin, Package, Plus, Sparkles, Wand2 } from 'lucide-react';

import {
  createLibEntity,
  extractLibEntitiesFromScript,
  listLibEntities,
  updateLibEntity,
  type LibEntity,
  type LibEntityType,
} from '../../services/libEntitiesService';
import {
  createCanvas,
  listCanvases,
} from '../../features/canvas-core/services/canvasService';
import { useToast } from '../Toast';

interface EntityLibraryProps {
  entityType: LibEntityType;
  projectId: string;
}

const META = {
  location: {
    Icon: MapPin,
    canvasSuffix: 'Location',
    coverClass: 'h-28 w-48', // wide establishing framing
  },
  prop: {
    Icon: Package,
    canvasSuffix: 'Prop',
    coverClass: 'h-36 w-36', // square hero-render framing
  },
} as const;

export function EntityLibrary({ entityType, projectId }: EntityLibraryProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId?: string }>();
  const { addToast } = useToast();
  const [rows, setRows] = useState<LibEntity[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const meta = META[entityType];
  const { Icon } = meta;
  const label = t(`libEntities.${entityType}.label`, entityType);

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    listLibEntities(projectId, entityType)
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((err) => {
        console.error('[EntityLibrary] load failed:', err);
        if (!cancelled) setRows([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, entityType]);

  const handleExtract = async () => {
    if (busy) return;
    setBusy('extract');
    try {
      setRows(await extractLibEntitiesFromScript(projectId, entityType));
    } catch (err) {
      console.error('[EntityLibrary] extract failed:', err);
      addToast(t('libEntities.extractFailed', 'Failed to extract from script'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handleCreate = async () => {
    if (busy) return;
    setBusy('new');
    try {
      const row = await createLibEntity(projectId, entityType, {
        name: t(`libEntities.${entityType}.untitled`, `New ${meta.canvasSuffix}`),
      });
      setRows((prev) => [...(prev ?? []), row]);
    } catch (err) {
      console.error('[EntityLibrary] create failed:', err);
      addToast(t('libEntities.createFailed', 'Failed to create'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handlePatch = async (
    row: LibEntity,
    fields: { name?: string; description?: string },
  ) => {
    setRows((prev) => prev?.map((r) => (r.id === row.id ? { ...r, ...fields } : r)) ?? prev);
    try {
      await updateLibEntity(projectId, entityType, row.id, fields);
    } catch (err) {
      console.error('[EntityLibrary] update failed:', err);
      addToast(t('libEntities.updateFailed', 'Failed to save'), 'error');
    }
  };

  const handleOpenCanvas = async (row: LibEntity) => {
    if (busy) return;
    setBusy(row.id);
    try {
      const wanted = `${row.name} · ${meta.canvasSuffix}`;
      const existing = (await listCanvases(projectId)).find(
        (c) => c.kind === entityType && c.name === wanted,
      );
      const canvas =
        existing ?? (await createCanvas(projectId, { name: wanted, kind: entityType }));
      const params = new URLSearchParams({
        entityId: row.id,
        name: row.name,
        description: row.description,
      });
      const base = teamId ? `/team/${teamId}/canvas/${canvas.id}` : `/canvas/${canvas.id}`;
      navigate(`${base}?${params.toString()}`);
    } catch (err) {
      console.error('[EntityLibrary] open canvas failed:', err);
      addToast(t('libEntities.openCanvasFailed', 'Failed to open canvas'), 'error');
      setBusy(null);
    }
  };

  if (rows === null) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Loader2 size={18} className="animate-spin text-ink-500" />
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div
        data-testid="entity-library-empty"
        className="flex flex-col items-center justify-center gap-3 py-16 text-center"
      >
        <Icon size={28} className="text-ink-500" />
        <p className="text-sm text-ink-300">
          {t(`libEntities.${entityType}.emptyTitle`, `No ${label}s yet`)}
        </p>
        <div className="flex gap-2">
          {entityType === 'location' && (
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
              {t('libEntities.extract', 'Extract from script')}
            </button>
          )}
          <button
            type="button"
            onClick={handleCreate}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-3 py-1.5 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
          >
            <Plus size={13} />
            {t(`libEntities.${entityType}.add`, `Add ${meta.canvasSuffix}`)}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div data-testid="entity-library">
      <div className="mb-3 flex items-center justify-end gap-2">
        {entityType === 'location' && (
          <button
            type="button"
            onClick={handleExtract}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
          >
            <Wand2 size={12} />
            {t('libEntities.extract', 'Extract from script')}
          </button>
        )}
        <button
          type="button"
          onClick={handleCreate}
          disabled={busy !== null}
          className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1 text-xs text-ink-300 hover:bg-ink-800 disabled:opacity-60"
        >
          <Plus size={12} />
          {t(`libEntities.${entityType}.add`, `Add ${meta.canvasSuffix}`)}
        </button>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {rows.map((row) => (
          <div
            key={row.id}
            data-testid="entity-card"
            className="rounded-2xl border border-ink-800 bg-ink-900/60 p-4"
          >
            <div className="flex gap-4">
              <div
                className={`${meta.coverClass} shrink-0 overflow-hidden rounded-xl border border-ink-800 bg-ink-950/50`}
              >
                {row.cover_url ? (
                  <img src={row.cover_url} alt={row.name} className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full w-full items-center justify-center text-ink-600">
                    <Icon size={30} />
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <input
                    className="min-w-0 flex-1 bg-transparent text-base font-semibold text-ink-100 outline-none focus:ring-1 focus:ring-indigo-500/40"
                    defaultValue={row.name}
                    aria-label={t('libEntities.nameLabel', 'Name')}
                    onBlur={(e) => {
                      const v = e.target.value.trim();
                      if (v && v !== row.name) void handlePatch(row, { name: v });
                    }}
                  />
                  {row.badge_tag && (
                    <span className="shrink-0 rounded-full bg-ink-800 px-2 py-0.5 text-[10px] font-medium text-ink-400">
                      {row.badge_tag}
                    </span>
                  )}
                </div>
                <textarea
                  className="mt-2 h-20 w-full resize-none bg-transparent text-xs leading-relaxed text-ink-300 outline-none placeholder:text-ink-600 focus:ring-1 focus:ring-indigo-500/40"
                  defaultValue={row.description}
                  placeholder={t('libEntities.bioPlaceholder', 'Look, mood, period, materials…')}
                  aria-label={t('libEntities.bioLabel', 'Description')}
                  onBlur={(e) => {
                    if (e.target.value !== row.description)
                      void handlePatch(row, { description: e.target.value });
                  }}
                />
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

            <div className="mt-3 flex items-center justify-between border-t border-ink-800 pt-3">
              <span className="text-[10px] text-ink-600">
                {t('libEntities.assetsHint', 'Generated assets appear here')}
              </span>
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
                {t('libEntities.openCanvas', 'Open in Canvas')}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
