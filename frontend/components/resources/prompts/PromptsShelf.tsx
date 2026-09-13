// frontend/components/resources/prompts/PromptsShelf.tsx
//
// The asset library's Prompts tab (spec §3.3), fed by the unified catalog.
// Replaces the always-empty `AssetShelf` render for `assetType === 'prompt'`.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';

import { useResourcesContext } from '../../../contexts/ResourcesContext';
import { fetchPrompts, promptText, thumbSrc, type PromptEntry, type PromptForm, type PromptLang, type PromptOrigin, type PromptPage, type PromptSlide } from '../../../services/promptsService';
import { fetchProjects } from '../../../services/projectsService';
import type { Project } from '../../../types';
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
/** One page, no pagination this PR (ruling R14). The shelf says so on screen
 *  when it hits the cap rather than letting a short grid sit under a much
 *  larger "All N" count.
 *
 *  Must not exceed the router's own ceiling (`limit: int = Query(60, ge=1,
 *  le=200)` in `prompts_router.py`) — a larger number is a 422, and a smaller
 *  one would make the notice below fire late. */
const PAGE_LIMIT = 200;

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
  const { scopeId, teamId, resPath, refreshAssetCounts } = useResourcesContext();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo<PromptFilters>(() => parsePromptFilters(searchParams), [searchParams]);
  const setFilters = useCallback((patch: Partial<PromptFilters>) => setSearchParams(serializePromptFilters({ ...filters, ...patch }), { replace: true }), [filters, setSearchParams]);

  const [projects, setProjects] = useState<Project[]>([]);
  const [page, setPage] = useState<PromptPage | null>(null);
  /** System presets, fetched separately — see the section's own comment. */
  const [presets, setPresets] = useState<PromptEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [tick, setTick] = useState(0);
  const [debouncedQ, setDebouncedQ] = useState(filters.q);
  const [lang] = useState<PromptLang>('en');
  const [send, setSend] = useState<{ entry: PromptEntry; slide?: PromptSlide } | null>(null);
  const [save, setSave] = useState<{ entry: PromptEntry; slideNames?: string[] } | null>(null);

  // The options for the project filter (ruling R13). Loaded once per mount;
  // a failure leaves the control usable so an active project filter can still
  // be cleared.
  useEffect(() => {
    let alive = true;
    fetchProjects(teamId ? { teamId } : undefined)
      .then((rows) => { if (alive) setProjects(rows); })
      .catch((err) => console.error('[PromptsShelf] project list unavailable:', err));
    return () => { alive = false; };
  }, [teamId]);

  // Typing is debounced, everything else is not (ruling R12). A chip click is
  // one deliberate act and should answer at once; a search box that fired per
  // keystroke sent one request per letter.
  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(filters.q), 300);
    return () => clearTimeout(id);
  }, [filters.q]);

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
      q: debouncedQ,
      limit: PAGE_LIMIT,
    })
      .then((p) => { if (!cancelled) setPage(p); })
      .catch((err) => { console.error('[PromptsShelf] load failed:', err); if (!cancelled) setLoadError(true); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [scopeId, filters.projectId, filters.form, filters.origin, debouncedQ, tick]);

  // System presets ride a SECOND request, because the catalog partitions by
  // segment and this shelf's own segment is `mine` (or `project`). Skipping
  // it was the whole bug: every preset prompt template was unreachable here
  // while the Assets tab beside it advertised them.
  //
  // Filtered with the same `q` as the main list — a section that ignored the
  // search box would answer a query with rows that do not match it — but NOT
  // with `origin`: presets are seeded, and an origin chip is a question about
  // where the user's own corpus came from.
  //
  // `wantsPresets` is both a correctness guard and the reason there is no
  // request to waste: every preset is a template, so a shelf narrowed to
  // Images or Albums must not answer with templates.
  const wantsPresets = filters.form === null || filters.form === 'template';
  useEffect(() => {
    if (!scopeId || !wantsPresets) { setPresets([]); return; }
    let cancelled = false;
    fetchPrompts(scopeId, { segment: 'system', form: 'template', q: debouncedQ, limit: PAGE_LIMIT })
      .then((p) => { if (!cancelled) setPresets(p.items); })
      // A failed preset fetch must not blank the shelf the user came for; the
      // section simply does not render (it is additive by construction).
      .catch((err) => { console.error('[PromptsShelf] presets unavailable:', err); if (!cancelled) setPresets([]); });
    return () => { cancelled = true; };
  }, [scopeId, wantsPresets, debouncedQ, tick]);

  const items = useMemo(() => sortEntries(page?.items ?? [], filters.sort), [page, filters.sort]);
  // Ruling R6: `total` describes the WHOLE unfiltered segment, so it — not the
  // filter state alone — decides which empty message is true. A filter that
  // matched nothing in a segment that holds nothing is still "no prompts yet";
  // telling that user to loosen filters would send them looking for rows that
  // do not exist.
  const filtered = !!(filters.form || filters.origin || debouncedQ.trim()) && (page?.total ?? 0) > 0;

  const onOpen = (entry: PromptEntry) =>
    navigate(resPath(entry.source.store === 'assets' ? `/resources/assets/item/${entry.source.id}` : `/resources/file/${entry.source.id}`));

  const sendText = send ? promptText(send.slide ?? send.entry, lang) : null;
  // Ruling R11: only a `resources` row may travel to the canvas as an id — it
  // is fetched back through /api/v1/resources/{id}/…. A template lives in
  // `assets`, so it sends its text alone. An album slide rides on the album's
  // resource id but carries its OWN picture, which is not the album cover.
  const sendResource = send && send.entry.source.store !== 'assets'
    ? { id: send.entry.source.id, filename: send.slide?.name ?? send.entry.title }
    : null;
  const sendCoverUrl = send?.slide?.url ? thumbSrc(send.slide.url) : undefined;

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
        <select aria-label={t('prompts.shelf.project', 'Project')} value={filters.projectId ?? ''}
          onChange={(e) => setFilters({ projectId: e.target.value || null })} className="rounded-lg border border-line bg-card px-2 py-0.5 text-[11px] text-content">
          <option value="">{t('prompts.shelf.allProjects', 'All projects')}</option>
          {projects.map((p) => <option key={p.id} value={String(p.id)}>{p.name}</option>)}
        </select>
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
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-2.5">
            {items.map((entry) =>
              entry.form === 'album'
                ? <PromptAlbumCard key={entry.key} entry={entry} lang={lang} onSend={(e, s) => setSend({ entry: e, slide: s })} onSaveAsTemplate={(e, names) => setSave({ entry: e, slideNames: names })} onOpen={onOpen} />
                : <PromptCard key={entry.key} entry={entry} lang={lang} onSend={(e) => setSend({ entry: e })} onSaveAsTemplate={(e) => setSave({ entry: e })} onOpen={onOpen} />,
            )}
          </div>
          {(page?.items.length ?? 0) >= PAGE_LIMIT && (
            <p className="py-2 text-[11px] text-content-3">{t('prompts.shelf.capped', { limit: PAGE_LIMIT, defaultValue: 'Showing the first {{limit}} — narrow with search or filters' })}</p>
          )}
        </>
      )}

      {/* Presets are global and read-only, and the form counts above describe
          the team's OWN corpus — `by_form` comes from the `mine`/`project`
          page and never sees these rows. Their own labelled section is what
          keeps the counts and the list above from disagreeing, exactly as
          `AssetShelf` does it for the other five asset types.

          Deliberately OUTSIDE the loading / empty / list conditional: a scope
          with no prompts of its own still has presets, and rendering "No
          prompts yet" over a shelf that does hold ten usable templates is the
          same silence this section exists to end. */}
      {presets.length > 0 && (
        <section data-testid="prompt-preset-section" className="mt-2">
          <div className="flex items-baseline gap-2 border-t border-line pt-4">
            <h3 className="text-[13px] font-medium text-content-2">{t('assets.presets.title', 'System Presets')}</h3>
            <p className="text-[11px] text-content-4">{t('assets.presets.hint', 'Read-only — duplicate one to edit it')}</p>
          </div>
          <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-2.5">
            {presets.map((entry) => (
              <PromptCard key={entry.key} entry={entry} lang={lang} onSend={(e) => setSend({ entry: e })} onSaveAsTemplate={(e) => setSave({ entry: e })} onOpen={onOpen} />
            ))}
          </div>
        </section>
      )}

      {send && sendText && (
        <SendToCanvasModal
          resource={sendResource}
          filename={send.slide?.name ?? send.entry.title}
          coverUrl={sendCoverUrl}
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
