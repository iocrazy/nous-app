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
// ⚠️ WHAT TO ACT ON IS ALWAYS AN ARGUMENT — never this component's own
// `activeKey` / `slideName` state read back from inside a handler (ruling R17,
// and the same hazard one level up for the entry). Two ways state goes stale
// under a handler that reads it:
//   - an album's per-slide Insert button changes the selection AND acts in the
//     same tick, so React has not committed the new `slideName` yet;
//   - the list stays clickable while the confirm sheet is open, so the row
//     selected when Replace is finally pressed can be a DIFFERENT prompt than
//     the one the sheet asked about — and the sheet's copy names only the
//     node, so nothing on screen would reveal the substitution.
// `textFor` therefore takes both the entry and the slide name; the confirm
// sheet snapshots both when it opens and hands them back to `applyNow`.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { BookmarkPlus, Plus } from 'lucide-react';
import { useOptionalToast } from '../../../components/Toast';
import { TemplateForm, type TemplateFormValue } from '../../../components/prompts/TemplateForm';
import { initialTemplateValue, shownSide } from '../../../components/prompts/SaveAsTemplateDialog';
import { promptText, saveAsTemplate, textForSide, PROMPT_FORMS, type PromptEntry, type PromptForm, type PromptLang, type PromptSegment } from '../../../services/promptsService';
import { useCanvasScope } from '../smart/canvasScope';
import { useCanvasReadOnly } from '../smart/nodes/useCanvasReadOnly';
import type { PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { ACTION_BTN, chipClass, type Label } from './libraryChrome';
import { LibraryPromptList } from './LibraryPromptList';
import { LibraryPromptPreview, activeSlide } from './LibraryPromptPreview';
import { useLibraryStore, type LibraryTarget } from './libraryStore';
import { titleFromBody } from './promptPanelTarget';
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

  const [activeKey, setActiveKey] = useState<string | null>(null);
  const [slideName, setSlideName] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [formValue, setFormValue] = useState<TemplateFormValue | null>(null);
  // Which language columns the open form's text belongs in — captured when the
  // form opens, from the side the prefill actually read (I3). `null` for
  // "Save current" / "New", whose text comes from a canvas node and has no
  // language, so it stays EN.
  const [formSide, setFormSide] = useState<PromptLang | null>(null);
  const [busy, setBusy] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  const catalog = usePromptCatalog({ scopeId, segment, projectId, form, query, enabled: open });
  const { reload } = catalog;
  const items = useMemo(() => {
    const all = catalog.page?.items ?? [];
    return group ? all.filter((e) => e.tags.includes(group)) : all;
  }, [catalog.page, group]);
  const groups = useMemo(() => groupChips(catalog.page?.items ?? []), [catalog.page]);
  const active = useMemo(() => items.find((e) => e.key === activeKey) ?? null, [items, activeKey]);
  useEffect(() => { setSlideName(null); }, [activeKey]);
  const live = !readOnly && target !== null && target.kind === 'prompt' && targetData !== null;
  const canAct = live;

  // The sheet NAMES the node it would overwrite, so it must not outlive it.
  // When the aimed node is deleted the panel clears the target and toasts;
  // `applyNow` would then early-return and Replace would sit there painted
  // live, doing nothing and saying nothing — the silent no-op this repo bans.
  // Only the CONFIRM goes: an open Save/New form holds text the user typed and
  // can still be saved without a target.
  useEffect(() => {
    if (!live) setSheet((s) => (s?.kind === 'confirm' ? null : s));
  }, [live]);

  // ── the four actions ─────────────────────────────────────────────────────
  // `name` is authoritative: `undefined` means "whatever `activeSlide` picks
  // for this entry", which is `null` (no slide) for anything but an album.
  const textFor = useCallback((entry: PromptEntry | null, name?: string) => {
    if (!entry) return null;
    return promptText(activeSlide(entry, name ?? null) ?? entry, lang);
  }, [lang]);

  const doInsert = useCallback((name?: string) => {
    const text = textFor(active, name);
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
  }, [textFor, active, live, target, targetData, toast, t]);

  const applyNow = useCallback((entry: PromptEntry | null, name?: string) => {
    const text = textFor(entry, name);
    if (!live || !target || !targetData || !text?.positive || !entry) return;
    const patch = buildApplyAllPatch({ positive: text.positive, negative: text.negative, params: entry.params, node: targetData });
    const { nodes, setNodes } = useCanvasCoreStore.getState();
    // ONE setNodes → one history entry → one ⌘Z brings body, negative and ratio back together.
    setNodes(nodes.map((n) => ((n as { id?: unknown }).id === target.nodeId
      ? { ...(n as Record<string, unknown>), data: { ...((n as { data?: Record<string, unknown> }).data ?? {}), ...patch } }
      : n)));
    setSheet(null);
  }, [textFor, live, target, targetData]);

  const doApplyAll = useCallback((name?: string) => {
    if (!active) return;
    const body = ((targetData?.body ?? '') as string).trim();
    if (body) setSheet({ kind: 'confirm', entry: active, slideName: name });
    else applyNow(active, name);
  }, [active, targetData, applyNow]);

  const openForm = useCallback((mode: 'current' | 'new' | 'promote', entry: PromptEntry | null) => {
    setFormSide(mode === 'promote' && entry ? shownSide(entry, undefined, lang) : null);
    if (mode === 'promote' && entry) {
      setFormValue(initialTemplateValue(entry, undefined, lang));
    } else if (mode === 'current' && targetData) {
      setFormValue({ title: titleFromBody(targetData.body as string | undefined, t('canvas.library.untitledPrompt', 'Prompt')), group: '', positive: (targetData.body ?? '') as string, negative: (targetData.negative_body ?? '') as string, exampleIds: [] });
    } else {
      setFormValue({ title: '', group: '', positive: '', negative: '', exampleIds: [] });
    }
    setSheet({ kind: 'form', mode, entry });
  }, [lang, targetData, t]);

  const submitForm = useCallback(async () => {
    if (!formValue || !formValue.title.trim() || !formValue.positive.trim()) return;
    setBusy(true);
    try {
      const { assetId } = await saveAsTemplate(scopeId, { title: formValue.title, group: formValue.group, exampleResourceIds: formValue.exampleIds, ...textForSide(formSide, formValue.positive, formValue.negative) });
      toast?.addToast(t('canvas.library.savedToMine', 'Saved to Mine'), 'success');
      setSheet(null);
      useLibraryStore.getState().setPromptSegment('mine');
      setActiveKey(`template:${assetId}`);
      reload();
    } catch (err) {
      console.error('[LibraryPromptsPage] save failed:', err);
      toast?.addToast(t('canvas.library.saveFailed', { code: (err as { code?: string })?.code ?? 'unknown', defaultValue: 'Could not save: {{code}}' }), 'error');
    } finally {
      setBusy(false);
    }
    // `catalog` is a fresh object every render, so depending on it made this
    // memo never hold; `catalog.reload` is the stable identity.
  }, [formValue, formSide, scopeId, toast, t, reload]);

  // Presets exist at all? Read by both the chip row and the Tab cycle, so the
  // two can never disagree about whether `system` is reachable.
  const showSystem = (catalog.counts?.system ?? 0) > 0;

  // ── keyboard (spec §3.4) ─────────────────────────────────────────────────
  const onKeyDown = (e: React.KeyboardEvent) => {
    const editing = (e.target as HTMLElement).closest('input, textarea');
    if (e.key === 'Escape') {
      if (sheet) { e.stopPropagation(); setSheet(null); return; }
      return; // LibraryPanel closes on Escape
    }
    // ↑↓ are navigation, not typing. The panel opens with focus in the search
    // box, so handing every key to the field made the arrow navigation spec
    // §3.4 advertises unavailable at exactly the moment the page opens — the
    // user had to click a row first. A multi-line textarea is the exception:
    // there ↑↓ move the caret between lines and belong to the field.
    const navKey = e.key === 'ArrowDown' || e.key === 'ArrowUp';
    if (editing && e.key !== 'Enter' && !(navKey && editing.tagName !== 'TEXTAREA')) return;
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
      // Built from the SAME predicates the chips are drawn with. `system` is
      // hidden without presets, and `project` is drawn but DISABLED without a
      // project — Tab bypassing that would set `segment=project` with a null
      // projectId, which the catalog answers with 422 `project_required`, and
      // the chip that could clear it is the disabled one.
      const order: PromptSegment[] = ['mine'];
      if (projectId) order.push('project');
      if (showSystem) order.push('system');
      useLibraryStore.getState().setPromptSegment(order[(order.indexOf(segment) + 1) % order.length]);
      return;
    }
    if (e.key === 'Enter' && !editing) {
      e.preventDefault();
      // The keyboard has no per-slide button to speak for it, so the CURRENT
      // selection is what it means — passed as the argument like every other
      // caller, never read back inside the action (ruling R17).
      const name = slideName ?? undefined;
      if (sheet?.kind === 'confirm') applyNow(sheet.entry, sheet.slideName);
      else if (e.shiftKey) doApplyAll(name);
      else doInsert(name);
    }
  };

  // Ruling R20. Three states in this order, because "pick a prompt node" is
  // advice a read-only viewer cannot take: read-only → no/invalid target →
  // live. `readOnlyConsequence` is the string the Media page already uses, so
  // both pages say the same thing about the same session.
  const readOnlyLine = t('canvas.library.readOnlyConsequence', 'Browse only · this canvas is read-only');
  const consequence = readOnly
    ? readOnlyLine
    : !live
      ? t('canvas.library.promptsNoTarget', 'Select a prompt node to insert or apply')
      : t('canvas.library.promptsConsequence', 'Insert positive adds to your text · Apply all replaces body, negative and params');
  // Ruling R6: `total` and `by_form` describe the WHOLE segment, not the
  // narrowed page — so this footer reads "of everything on this shelf".
  const fromPictures = (catalog.page?.by_form.image ?? 0) + (catalog.page?.by_form.album ?? 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="library-prompts-page" onKeyDown={onKeyDown} tabIndex={-1}>
      <div className="flex items-center gap-1 px-2 pt-1.5">
        <button type="button" className={chipClass(segment === 'mine')} aria-pressed={segment === 'mine'} onClick={() => useLibraryStore.getState().setPromptSegment('mine')}>{t('canvas.library.segmentMine', 'Mine')}{catalog.counts ? ` ${catalog.counts.mine}` : ''}</button>
        <button type="button" className={chipClass(segment === 'project')} aria-pressed={segment === 'project'} disabled={!projectId} onClick={() => useLibraryStore.getState().setPromptSegment('project')}>{t('canvas.library.segmentProject', 'This project')}{catalog.counts?.project != null ? ` ${catalog.counts.project}` : ''}</button>
        {showSystem && <button type="button" className={chipClass(segment === 'system')} aria-pressed={segment === 'system'} onClick={() => useLibraryStore.getState().setPromptSegment('system')}>{t('canvas.library.segmentSystem', 'System')}</button>}
        <span className="flex-1" />
        {/* The two ways to PUT something on this shelf, beside the chips that
            say which shelf is showing — both write into Mine. They used to sit
            in the bottom status strip next to the count, which is the row that
            describes the shelf rather than acts on it (user screenshot).

            `rounded-lg` + a card fill + an icon, so they do not read as two
            more filter pills: the pills are round, flat and text-only. The
            border is the load-bearing part — these two carried
            `border-transparent` and so rendered as two lines of plain text on
            the dark ground, which is exactly what was reported. */}
        <div data-testid="library-prompt-actions" className="flex shrink-0 items-center gap-1">
          <button type="button" className={ACTION_BTN} disabled={!live || !!sheet}
            title={live ? undefined : t('canvas.library.pickPromptNodeFirst', 'Pick a prompt node first')}
            onClick={() => openForm('current', null)}>
            <BookmarkPlus size={11} aria-hidden />
            {t('canvas.library.saveCurrent', 'Save current…')}
          </button>
          <button type="button" className={ACTION_BTN} disabled={readOnly || !!sheet} onClick={() => openForm('new', null)}>
            <Plus size={11} aria-hidden />
            {t('canvas.library.newTemplate', 'New…')}
          </button>
        </div>
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

      {/* `grid-rows-[minmax(0,1fr)]` and `min-h-0` on both columns, mirroring
          how the Media page bounds LibraryGrid. Without them the implicit row
          is content-sized — grid distributes only POSITIVE free space — so a
          long shelf grows past the panel and `LibraryPanel`'s own
          `overflow-hidden` clips it over the footer with no scrollbar. The
          scroll lives on the column wrapper because `LibraryPromptList` takes
          no className; its own `overflow-y-auto` is then inert. */}
      <div className="grid min-h-0 flex-1 grid-cols-[250px_1fr] grid-rows-[minmax(0,1fr)] overflow-hidden">
        <div className="nowheel flex min-h-0 min-w-0 flex-col overflow-y-auto border-r border-canvas-line">
          <LibraryPromptList items={items} activeKey={activeKey} onActivate={setActiveKey} lang={lang} loading={catalog.loading} error={catalog.error} onRetry={catalog.reload}
            emptyLabel={query.trim() || form || group ? t('canvas.library.promptsNoMatch', 'No prompts match') : t('canvas.library.promptsEmpty', 'No prompts yet — upload a picture that carries generation metadata, run a caption, or save the aimed node with Save current.')} />
        </div>
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
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
                canAct={canAct} actHint={readOnly ? readOnlyLine : t('canvas.library.pickPromptNodeFirst', 'Pick a prompt node first')}
                canSaveAsTemplate={!readOnly && !sheet} onInsert={doInsert} onApplyAll={doApplyAll} onSaveAsTemplate={() => active && openForm('promote', active)} />
              {sheet?.kind === 'confirm' && (
                <div data-testid="library-prompt-confirm" className="mx-2.5 mb-2 rounded-lg border border-warn bg-warn-soft px-2.5 py-2 text-[11px] text-canvas-text">
                  <b>{t('canvas.library.replaceBodyQ', { title: target?.title ?? '', defaultValue: 'Replace the body of 《{{title}}》?' })}</b><br />
                  {t('canvas.library.replaceBodyNote', 'Its text, negative and params are overwritten. Undo with ⌘Z.')}
                  <div className="mt-1.5 flex gap-1.5">
                    <button type="button" className="nodrag rounded-lg bg-danger px-2.5 py-1 text-[11px] font-medium text-white" onClick={() => applyNow(sheet.entry, sheet.slideName)}>{t('canvas.library.replace', 'Replace')}</button>
                    <button type="button" className="nodrag rounded-lg border border-canvas-line px-2.5 py-1 text-[11px]" onClick={() => setSheet(null)}>{t('canvas.library.cancel', 'Cancel')}</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Pure status strip now — it describes the shelf, it does not act on
          it. Left-aligned because a lone count pushed to the right edge with
          nothing beside it reads as something that lost its neighbours. */}
      <div data-testid="library-prompts-footer" className="border-t border-canvas-line px-2 py-1.5 text-[10.5px] text-canvas-muted">
        {catalog.page && t('canvas.library.promptsFooter', { count: catalog.page.total, pictures: fromPictures, defaultValue: '{{count}} prompts · {{pictures}} from pictures' })}
      </div>
    </div>
  );
}
