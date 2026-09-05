// features/canvas-core/library/LibraryPromptsPage.tsx
//
// The panel's Prompts page (spec 2026-09-05 §3.4). Same shell as the Media
// page; the body is a list + preview over the unified catalog. Four actions:
//   Insert positive — editor handle insertText (fallback: append via patchNode)
//   Apply all       — inline confirm when the body is non-empty; ONE setNodes
//   Save current    — TemplateForm prefilled from the aimed node
//   New             — TemplateForm blank
// plus Save as template on any picture row (spec §3.5).
//
// ⚠️ THE SLIDE TO ACT ON IS ALWAYS AN ARGUMENT, never this component's own
// `slideName` state read from inside a handler (ruling R17). An album's
// per-slide Insert button changes the selection AND acts in the same tick, so
// a handler reading its own state would insert the PREVIOUSLY selected slide.
// `textFor` therefore takes the name; `doInsert` / `doApplyAll` / `applyNow`
// thread it through, and the confirm sheet REMEMBERS the slide it was opened
// for so Replace applies that one rather than whatever is selected by then.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useOptionalToast } from '../../../components/Toast';
import { TemplateForm, type TemplateFormValue } from '../../../components/prompts/TemplateForm';
import { initialTemplateValue } from '../../../components/prompts/SaveAsTemplateDialog';
import { promptText, saveAsTemplate, PROMPT_FORMS, type PromptEntry, type PromptForm, type PromptLang, type PromptSegment } from '../../../services/promptsService';
import { useCanvasScope } from '../smart/canvasScope';
import { useCanvasReadOnly } from '../smart/nodes/useCanvasReadOnly';
import type { PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { chipClass, type Label } from './libraryChrome';
import { LibraryPromptList } from './LibraryPromptList';
import { LibraryPromptPreview, activeSlide } from './LibraryPromptPreview';
import { useLibraryStore, type LibraryTarget } from './libraryStore';
import { EditorGoneError, getMentionHandle } from './mentionHandles';
import { appendPositive, buildApplyAllPatch, groupChips } from './promptActions';
import { usePromptCatalog } from './usePromptCatalog';

const FORM_LABEL: Record<PromptForm, Label> = {
  template: ['canvas.library.formTemplates', 'Templates'],
  image: ['canvas.library.formImages', 'Images'],
  album: ['canvas.library.formAlbums', 'Albums'],
};

type Sheet =
  | { kind: 'confirm'; entry: PromptEntry; slideName?: string }
  | { kind: 'form'; mode: 'current' | 'new' | 'promote'; entry: PromptEntry | null }
  | null;

export function LibraryPromptsPage({ target, targetData }: { target: LibraryTarget | null; targetData: PromptNodeData | null }): React.ReactElement {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const { scopeId } = useCanvasScope();
  const projectId = useCanvasCoreStore((s) => s.projectId);
  const readOnly = useCanvasReadOnly();
  const open = useLibraryStore((s) => s.open);
  const query = useLibraryStore((s) => s.query);
  const segment = useLibraryStore((s) => s.promptSegment);
  const form = useLibraryStore((s) => s.promptForm);
  const lang = useLibraryStore((s) => s.promptLang);
  const focusNonce = useLibraryStore((s) => s.focusNonce);

  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [slideName, setSlideName] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [formValue, setFormValue] = useState<TemplateFormValue | null>(null);
  const [busy, setBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const catalog = usePromptCatalog({ scopeId, segment, projectId, form, query, enabled: open });
  const items = useMemo(() => {
    const all = catalog.page?.items ?? [];
    return group ? all.filter((e) => e.tags.includes(group)) : all;
  }, [catalog.page, group]);
  const groups = useMemo(() => groupChips(catalog.page?.items ?? []), [catalog.page]);
  const active = useMemo(() => items.find((e) => e.key === activeKey) ?? null, [items, activeKey]);
  useEffect(() => { setSlideName(null); }, [activeKey]);
  useEffect(() => { if (focusNonce) searchRef.current?.focus(); }, [focusNonce]);

  const live = !readOnly && target !== null && target.kind === 'prompt' && targetData !== null;
  const canAct = live;

  // ── the four actions ─────────────────────────────────────────────────────
  // `name` is authoritative: `undefined` means "whatever `activeSlide` picks
  // for this entry", which is `null` (no slide) for anything but an album.
  const textFor = useCallback((name?: string) => {
    if (!active) return null;
    return promptText(activeSlide(active, name ?? null) ?? active, lang);
  }, [active, lang]);

  const doInsert = useCallback((name?: string) => {
    const text = textFor(name);
    if (!live || !target || !text?.positive) return;
    const handle = getMentionHandle(target.nodeId);
    try {
      if (!handle) throw new EditorGoneError();
      handle.insertText(text.positive);
    } catch (err) {
      if (!(err instanceof EditorGoneError)) throw err;
      // The card is culled off-viewport: append instead, and say so.
      const body = (targetData?.body ?? '') as string;
      useCanvasCoreStore.getState().patchNode(target.nodeId, { data: { body: appendPositive(body, text.positive) } });
      toast?.addToast(t('canvas.library.insertedAtEnd', 'Added to the end — the card was off screen'), 'info');
    }
  }, [textFor, live, target, targetData, toast, t]);

  const applyNow = useCallback((name?: string) => {
    const text = textFor(name);
    if (!live || !target || !targetData || !text?.positive || !active) return;
    const patch = buildApplyAllPatch({ positive: text.positive, negative: text.negative, params: active.params, node: targetData });
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    // ONE setNodes → one history entry → one ⌘Z brings body, negative and ratio back together.
    setNodes(nodes.map((n) => ((n as { id?: unknown }).id === target.nodeId
      ? { ...(n as Record<string, unknown>), data: { ...((n as { data?: Record<string, unknown> }).data ?? {}), ...patch } }
      : n)));
    setSheet(null);
  }, [textFor, live, target, targetData, active]);

  const doApplyAll = useCallback((name?: string) => {
    if (!active) return;
    const body = ((targetData?.body ?? '') as string).trim();
    if (body) setSheet({ kind: 'confirm', entry: active, slideName: name });
    else applyNow(name);
  }, [active, targetData, applyNow]);

  const openForm = useCallback((mode: 'current' | 'new' | 'promote', entry: PromptEntry | null) => {
    if (mode === 'promote' && entry) {
      setFormValue(initialTemplateValue(entry, undefined, lang));
    } else if (mode === 'current' && targetData) {
      setFormValue({ title: ((targetData.body ?? '') as string).split('\n')[0].slice(0, 40) || 'Prompt', group: '', positive: (targetData.body ?? '') as string, negative: (targetData.negative_body ?? '') as string, exampleIds: [] });
    } else {
      setFormValue({ title: '', group: '', positive: '', negative: '', exampleIds: [] });
    }
    setSheet({ kind: 'form', mode, entry });
  }, [lang, targetData]);

  const submitForm = useCallback(async () => {
    if (!formValue || !formValue.title.trim() || !formValue.positive.trim()) return;
    setBusy(true);
    try {
      const { assetId } = await saveAsTemplate(scopeId, { title: formValue.title, group: formValue.group, positive: formValue.positive, negative: formValue.negative, exampleResourceIds: formValue.exampleIds });
      toast?.addToast(t('canvas.library.savedToMine', 'Saved to Mine'), 'success');
      setSheet(null);
      useLibraryStore.getState().setPromptSegment('mine');
      setActiveKey(`template:${assetId}`);
      catalog.reload();
    } catch (err) {
      console.error('[LibraryPromptsPage] save failed:', err);
      toast?.addToast(t('canvas.library.saveFailed', { code: (err as { code?: string })?.code ?? 'unknown', defaultValue: 'Could not save: {{code}}' }), 'error');
    } finally {
      setBusy(false);
    }
  }, [formValue, scopeId, toast, t, catalog]);

  // ── keyboard (spec §3.4) ─────────────────────────────────────────────────
  const onKeyDown = (e: React.KeyboardEvent) => {
    const editing = (e.target as HTMLElement).closest('input, textarea');
    if (e.key === 'Escape') {
      if (sheet) { e.stopPropagation(); setSheet(null); return; }
      return; // LibraryPanel closes on Escape
    }
    if (editing && e.key !== 'Enter') return;
    if (e.key === '/' && !editing) { e.preventDefault(); searchRef.current?.focus(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = items.findIndex((it) => it.key === activeKey);
      const next = items[Math.min(items.length - 1, Math.max(0, idx + (e.key === 'ArrowDown' ? 1 : -1)))];
      if (next) setActiveKey(next.key);
      return;
    }
    if ((e.key === 'ArrowRight' || e.key === 'ArrowLeft') && active?.slides) {
      e.preventDefault();
      const s = active.slides; const cur = activeSlide(active, slideName);
      const i = s.findIndex((x) => x.name === cur?.name);
      const n = s[Math.min(s.length - 1, Math.max(0, i + (e.key === 'ArrowRight' ? 1 : -1)))];
      if (n) setSlideName(n.name);
      return;
    }
    if (e.key === 'Tab' && !editing) {
      e.preventDefault();
      const order: PromptSegment[] = catalog.counts && catalog.counts.system > 0 ? ['mine', 'project', 'system'] : ['mine', 'project'];
      useLibraryStore.getState().setPromptSegment(order[(order.indexOf(segment) + 1) % order.length]);
      return;
    }
    if (e.key === 'Enter' && !editing) {
      e.preventDefault();
      // The keyboard has no per-slide button to speak for it, so the CURRENT
      // selection is what it means — passed as the argument like every other
      // caller, never read back inside the action (ruling R17).
      const name = slideName ?? undefined;
      if (sheet?.kind === 'confirm') applyNow(sheet.slideName);
      else if (e.shiftKey) doApplyAll(name);
      else doInsert(name);
    }
  };

  const consequence = !live
    ? t('canvas.library.promptsNoTarget', 'Select a prompt node to insert or apply')
    : t('canvas.library.promptsConsequence', 'Insert positive adds to your text · Apply all replaces body, negative and params');
  const showSystem = (catalog.counts?.system ?? 0) > 0;
  // Ruling R6: `total` and `by_form` describe the WHOLE segment, not the
  // narrowed page — so this footer reads "of everything on this shelf".
  const fromPictures = (catalog.page?.by_form.image ?? 0) + (catalog.page?.by_form.album ?? 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-prompts-page" onKeyDown={onKeyDown} tabIndex={-1}>
      <div className="flex items-center gap-1 px-2 pt-1.5">
        <button type="button" className={chipClass(segment === 'mine')} aria-pressed={segment === 'mine'} onClick={() => useLibraryStore.getState().setPromptSegment('mine')}>{t('canvas.library.segmentMine', 'Mine')}{catalog.counts ? ` ${catalog.counts.mine}` : ''}</button>
        <button type="button" className={chipClass(segment === 'project')} aria-pressed={segment === 'project'} disabled={!projectId} onClick={() => useLibraryStore.getState().setPromptSegment('project')}>{t('canvas.library.segmentProject', 'This project')}{catalog.counts?.project != null ? ` ${catalog.counts.project}` : ''}</button>
        {showSystem && <button type="button" className={chipClass(segment === 'system')} aria-pressed={segment === 'system'} onClick={() => useLibraryStore.getState().setPromptSegment('system')}>{t('canvas.library.segmentSystem', 'System')}</button>}
      </div>
      <div className="flex items-center gap-1.5 border-b border-canvas-line px-2 py-1.5">
        <input ref={searchRef} data-testid="library-search" aria-label={t('canvas.library.searchPrompts', 'Search prompts')} placeholder={t('canvas.library.searchPrompts', 'Search prompts')} value={query}
          onChange={(e) => useLibraryStore.getState().setQuery(e.target.value)} className="nodrag w-full bg-transparent text-[11px] text-canvas-text outline-none placeholder:text-canvas-muted" />
      </div>
      <div data-testid="library-consequence" className="border-b border-canvas-line px-2 py-1 text-[10.5px] text-canvas-muted">{consequence}</div>
      <div className="flex items-center gap-1 overflow-x-auto border-b border-canvas-line px-2 py-1.5">
        <button type="button" className={chipClass(form === null)} onClick={() => useLibraryStore.getState().setPromptForm(null)}>{t('canvas.library.formAll', 'All')}</button>
        {PROMPT_FORMS.map((f) => <button key={f} type="button" className={chipClass(form === f)} onClick={() => useLibraryStore.getState().setPromptForm(form === f ? null : f)}>{t(FORM_LABEL[f][0], FORM_LABEL[f][1])}</button>)}
        {groups.length > 0 && <span className="mx-1 h-3.5 w-px bg-canvas-line" />}
        {groups.map((g) => <button key={g} type="button" className={chipClass(group === g)} onClick={() => setGroup(group === g ? null : g)}>{g}</button>)}
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-[250px_1fr]">
        <div className="min-w-0 border-r border-canvas-line">
          <LibraryPromptList items={items} activeKey={activeKey} onActivate={setActiveKey} lang={lang} loading={catalog.loading} error={catalog.error} onRetry={catalog.reload}
            emptyLabel={query.trim() || form || group ? t('canvas.library.promptsNoMatch', 'No prompts match') : t('canvas.library.promptsEmpty', 'No prompts yet — upload a picture that carries generation metadata, run a caption, or save the aimed node with Save current.')} />
        </div>
        <div className="flex min-w-0 flex-col">
          {sheet?.kind === 'form' && formValue ? (
            <div className="flex min-h-0 flex-1 flex-col">
              <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                <TemplateForm value={formValue} onChange={setFormValue} groups={groups}
                  examples={sheet.entry && sheet.entry.form !== 'template' ? [{ id: sheet.entry.source.id, url: sheet.entry.thumbs[0]?.url ?? null }] : []} disabled={busy} />
              </div>
              <div className="flex items-center gap-1.5 border-t border-canvas-line px-2.5 py-2">
                <button type="button" className="nodrag rounded-lg bg-[var(--accent-text)] px-2.5 py-1 text-[11px] font-medium text-white disabled:opacity-40" disabled={busy || !formValue.title.trim() || !formValue.positive.trim()} onClick={() => void submitForm()}>{t('canvas.library.saveToMine', 'Save to Mine')}</button>
                <button type="button" className="nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px]" onClick={() => setSheet(null)}>{t('canvas.library.cancel', 'Cancel')}</button>
                <span className="flex-1" />
                <span className="text-[10.5px] text-canvas-muted">{t('canvas.library.filesStay', 'Pictures stay where they are')}</span>
              </div>
            </div>
          ) : (
            <>
              <LibraryPromptPreview entry={active} lang={lang} onLangChange={(l: PromptLang) => useLibraryStore.getState().setPromptLang(l)} slideName={slideName} onSlideChange={setSlideName}
                canAct={canAct} actHint={t('canvas.library.pickPromptNodeFirst', 'Pick a prompt node first')} onInsert={doInsert} onApplyAll={doApplyAll} onSaveAsTemplate={() => active && openForm('promote', active)} />
              {sheet?.kind === 'confirm' && (
                <div data-testid="library-prompt-confirm" className="mx-2.5 mb-2 rounded-lg border border-warn bg-warn-soft px-2.5 py-2 text-[11px] text-canvas-text">
                  <b>{t('canvas.library.replaceBodyQ', { title: target?.title ?? '', defaultValue: 'Replace the body of 《{{title}}》?' })}</b><br />
                  {t('canvas.library.replaceBodyNote', 'Its text, negative and params are overwritten. Undo with ⌘Z.')}
                  <div className="mt-1.5 flex gap-1.5">
                    <button type="button" className="nodrag rounded-lg bg-danger px-2.5 py-1 text-[11px] font-medium text-white" onClick={() => applyNow(sheet.slideName)}>{t('canvas.library.replace', 'Replace')}</button>
                    <button type="button" className="nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px]" onClick={() => setSheet(null)}>{t('canvas.library.cancel', 'Cancel')}</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 border-t border-canvas-line px-2 py-1.5 text-[10.5px] text-canvas-muted">
        <button type="button" className="nodrag rounded-lg border border-transparent px-2 py-0.5 text-[11px] text-canvas-text disabled:opacity-40" disabled={!live || !!sheet} onClick={() => openForm('current', null)}>{t('canvas.library.saveCurrent', 'Save current…')}</button>
        <button type="button" className="nodrag rounded-lg border border-transparent px-2 py-0.5 text-[11px] text-canvas-text disabled:opacity-40" disabled={readOnly || !!sheet} onClick={() => openForm('new', null)}>{t('canvas.library.newTemplate', 'New…')}</button>
        <span className="flex-1" />
        {catalog.page && t('canvas.library.promptsFooter', { count: catalog.page.total, pictures: fromPictures, defaultValue: '{{count}} prompts · {{pictures}} from pictures' })}
      </div>
    </div>
  );
}
