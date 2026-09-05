// frontend/components/resources/prompts/PromptsShelf.tsx
//
// The asset library's Prompts tab (spec §3.3), fed by the unified catalog.
// Replaces the always-empty `AssetShelf` render for `assetType === 'prompt'`.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { fetchPrompts, promptText, type PromptEntry, type PromptForm, type PromptLang, type PromptOrigin, type PromptPage, type PromptSlide } from '../../../services/promptsService';
import type { Resource } from '../../../types';
import { SendToCanvasModal } from '../SendToCanvasModal';
import { SaveAsTemplateDialog } from '../../prompts/SaveAsTemplateDialog';
import { PromptAlbumCard } from './PromptAlbumCard';
import { PromptCard } from './PromptCard';
import { parsePromptFilters, serializePromptFilters, sortEntries, type PromptFilters } from './promptFilters';

const FORM_LABEL: Record<PromptForm, [string, string]> = {
  template: ['prompts.shelf.formTemplates', 'Templates'],
  image: ['prompts.shelf.formImages', 'Images'],
  album: ['prompts.shelf.formAlbums', 'Albums'],
};
const ORIGIN_LABEL: Record<PromptOrigin, [string, string]> = {
  typed: ['prompts.origin.typed', 'Typed'],
  extracted: ['prompts.origin.extracted', 'Extracted'],
  captioned: ['prompts.origin.captioned', 'Captioned'],
};

function Seg({ on, label, count, onClick }: { on: boolean; label: string; count?: number; onClick: () => void }) {
  const text = count === undefined ? label : `${label} ${count}`;
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={`border-r border-line px-2.5 py-0.5 text-[11px] last:border-r-0 ${on ? 'bg-accent-soft text-accent' : 'text-content-3'}`}>
      {text}
    </button>
  );
}

export const PromptsShelf: React.FC = () => {
  const { t } = useTranslation();
  const { scopeId, resPath, refreshAssetCounts } = useResourcesContext();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo<PromptFilters>(() => parsePromptFilters(searchParams), [searchParams]);
  const setFilters = useCallback((patch: Partial<PromptFilters>) => setSearchParams(serializePromptFilters({ ...filters, ...patch }), { replace: true }), [filters, setSearchParams]);

  const [page, setPage] = useState<PromptPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [tick, setTick] = useState(0);
  const [lang] = useState<PromptLang>('en');
  const [send, setSend] = useState<{ entry: PromptEntry; slide?: PromptSlide } | null>(null);
  const [save, setSave] = useState<{ entry: PromptEntry; slideNames?: string[] } | null>(null);

  useEffect(() => {
    if (!scopeId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    fetchPrompts(scopeId, {
      segment: filters.projectId ? 'project' : 'mine',
      projectId: filters.projectId,
      form: filters.form,
      origin: filters.origin,
      q: filters.q,
      limit: 200,
    })
      .then((p) => { if (!cancelled) setPage(p); })
      .catch((err) => { console.error('[PromptsShelf] load failed:', err); if (!cancelled) setLoadError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scopeId, filters.projectId, filters.form, filters.origin, filters.q, tick]);

  const items = useMemo(() => sortEntries(page?.items ?? [], filters.sort), [page, filters.sort]);
  // Ruling R6: `total` describes the WHOLE unfiltered segment, so it — not the
  // filter state alone — decides which empty message is true. A filter that
  // matched nothing in a segment that holds nothing is still "no prompts yet";
  // telling that user to loosen filters would send them looking for rows that
  // do not exist.
  const filtered = !!(filters.form || filters.origin || filters.q.trim()) && (page?.total ?? 0) > 0;

  const onOpen = (entry: PromptEntry) =>
    navigate(resPath(entry.source.store === 'assets' ? `/resources/assets/item/${entry.source.id}` : `/resources/file/${entry.source.id}`));

  const sendText = send ? promptText(send.slide ?? send.entry, lang) : null;

  return (
    <div className="flex h-full flex-col gap-2.5 p-4" data-testid="prompts-shelf">
      <div className="flex flex-wrap items-center gap-1.5">
        <div className="flex overflow-hidden rounded-lg border border-line">
          <Seg on={filters.form === null} label={t('prompts.shelf.all', 'All')} count={page?.total} onClick={() => setFilters({ form: null })} />
          {(Object.keys(FORM_LABEL) as PromptForm[]).map((f) => (
            <Seg key={f} on={filters.form === f} label={t(FORM_LABEL[f][0], FORM_LABEL[f][1])} count={page?.by_form[f]} onClick={() => setFilters({ form: filters.form === f ? null : f })} />
          ))}
        </div>
        <div className="flex overflow-hidden rounded-lg border border-line">
          <Seg on={filters.origin === null} label={t('prompts.shelf.anyOrigin', 'Any origin')} onClick={() => setFilters({ origin: null })} />
          {(Object.keys(ORIGIN_LABEL) as PromptOrigin[]).map((o) => (
            <Seg key={o} on={filters.origin === o} label={t(ORIGIN_LABEL[o][0], ORIGIN_LABEL[o][1])} count={page?.by_origin[o]} onClick={() => setFilters({ origin: filters.origin === o ? null : o })} />
          ))}
        </div>
        <input aria-label={t('prompts.shelf.search', 'Search prompts')} placeholder={t('prompts.shelf.search', 'Search prompts')} value={filters.q}
          onChange={(e) => setFilters({ q: e.target.value })} className="rounded-lg border border-line bg-card px-2 py-0.5 text-[11px] text-content outline-none" />
        <select aria-label={t('prompts.shelf.sort', 'Sort')} value={filters.sort} onChange={(e) => setFilters({ sort: e.target.value as PromptFilters['sort'] })} className="rounded-lg border border-line bg-card px-2 py-0.5 text-[11px] text-content">
          <option value="recent">{t('prompts.shelf.sortRecent', 'Recently updated')}</option>
          <option value="title">{t('prompts.shelf.sortTitle', 'Title')}</option>
        </select>
        <span className="flex-1" />
        <button type="button" className="rounded-lg border border-line px-2.5 py-0.5 text-[11px]" onClick={() => setSave({ entry: emptyEntry() })}>{t('prompts.shelf.newTemplate', 'New template')}</button>
      </div>

      {loading && !page ? (
        <div className="flex items-center gap-2 py-10 text-sm text-content-3"><Loader2 size={14} className="animate-spin" />{t('common.loading', 'Loading...')}</div>
      ) : loadError ? (
        <div className="flex items-center gap-2 py-10 text-sm text-danger">
          {t('prompts.shelf.loadFailed', 'Could not load prompts')}
          <button type="button" className="rounded-md border border-line px-2 py-0.5 text-[11px] text-content" onClick={() => setTick((v) => v + 1)}>{t('common.retry', 'Retry')}</button>
        </div>
      ) : items.length === 0 ? (
        <p className="py-10 text-sm text-content-3">
          {filtered
            ? t('prompts.shelf.emptyFiltered', 'No prompts match these filters')
            : t('prompts.shelf.emptyNone', 'No prompts yet — upload a picture that carries generation metadata, run a caption, or save one from a canvas.')}
        </p>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-2.5">
          {items.map((entry) =>
            entry.form === 'album'
              ? <PromptAlbumCard key={entry.key} entry={entry} lang={lang} onSend={(e, s) => setSend({ entry: e, slide: s })} onSaveAsTemplate={(e, names) => setSave({ entry: e, slideNames: names })} onOpen={onOpen} />
              : <PromptCard key={entry.key} entry={entry} lang={lang} onSend={(e) => setSend({ entry: e })} onSaveAsTemplate={(e) => setSave({ entry: e })} onOpen={onOpen} />,
          )}
        </div>
      )}

      {send && sendText && (
        <SendToCanvasModal
          resource={{ id: send.entry.source.id, filename: send.entry.title } as unknown as Resource}
          positive={sendText.positive}
          negative={sendText.negative}
          onClose={() => setSend(null)}
        />
      )}
      {save && (
        <SaveAsTemplateDialog
          scopeId={scopeId}
          entry={save.entry}
          slideNames={save.slideNames}
          lang={lang}
          onClose={() => setSave(null)}
          onSaved={() => { setSave(null); refreshAssetCounts(); setTick((v) => v + 1); }}
        />
      )}
    </div>
  );
};

/** "New template" starts from nothing; the dialog treats a template-form entry with no text as blank. */
function emptyEntry(): PromptEntry {
  return { key: 'template:new', form: 'template', origin: 'typed', title: '', tags: [], positive_en: null, positive_zh: null, negative_en: null, negative_zh: null, params: null, thumbs: [], slides: null, source: { store: 'assets', id: '' }, updated_at: '' };
}
