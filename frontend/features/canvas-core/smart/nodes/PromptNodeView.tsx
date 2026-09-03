import { mediaSrc } from '../mediaUrl';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';

import { NodeDeleteButton } from './NodeDeleteButton';
import { ImagePlus, Library, Play, Split, Square, Zap } from 'lucide-react';

import type { CanvasConnection, CanvasNode } from '../../types';
import type { DroppedRef, GeneratedImageRef, PromptGenSettings, PromptNodeData, PromptResourceRef } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useGenerationModels } from './useGenerationModels';
import { useTextModels } from './useTextModels';
import { useAgents } from './useAgents';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';
import { startChainRun, useChainRunStore } from '../chainRun';
import { splitPromptItems } from '../promptSplit';
import { rerunPrompt } from '../regenerate';
import {
  useIsChainTail,
  useNodeInputUrls,
  useUpstreamAssetRefCount,
  useUpstreamPromptText,
} from './useGraphDerived';
import { MAX_REFERENCE_IMAGES, reorderRefs } from '../refOrder';
import { RunStatusBadge } from './RunStatusBadge';
import {
  elapsedSeconds,
  formatElapsed,
  useElapsedSeconds,
} from './elapsed';
import { useCanvasMentionPicker } from './useCanvasMentionPicker';
import { PromptBodyEditor, type PromptBodyEditorHandle } from './PromptBodyEditor';
import { addReferences } from '../../library/addReferences';
import { dropConsequenceKey, dropLibraryItems, hasLibraryDrag, readLibraryDrag, type LibraryDropTarget } from '../../library/dropLibraryItems';
import { useLibraryStore } from '../../library/libraryStore';
import type { PromptImageRef } from './promptImageRefs';
import {
  PromptMentionPicker,
  type PromptMentionPickerHandle,
} from './PromptMentionPicker';
import { useCanvasScope } from '../canvasScope';
import type { MentionedAsset } from '../mentionedAssets';
import { useModelCapabilities } from './useModelCapabilities';
import { fetchAssetDetail, type AssetSummary } from '../../../../services/assetsService';
import { primarySlotFileIds } from '../assetFiles';
import { promptStripEntries } from '../promptStrip';
import { ASSET_TYPE_ICON } from '../../../../components/resources/assets/assetTypeMeta';
import { GenFooterControls } from './GenFooterControls';
import { AssetPromptPicker } from './AssetPromptPicker';
import { buildPromptAssetLoad } from '../loadPromptAsset';
import { importResourceAsCanvasMedia } from '../mediaImport';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { getResourceCoverUrl, type PromptAsset } from '../../../../services/resourceService';
import { ASPECT_RATIOS } from '../aspectPresets';
import { UiSelect } from '../../../../components/ui';

/** Hit area of the browser's `resize` grip, in CSS px. */
const GRIP_PX = 18;

// Canvas pill trigger — keeps the node's ghost/rounded look while borrowing the
// shared UiSelect portal menu (fixes the native popup covering the trigger).
import { CANVAS_PILL_TRIGGER } from './canvasPill';

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const {
    body,
    body_h,
    split_enabled,
    split_separator,
    provider_slug,
    agent_id = null,
    run_status,
    run_error,
    run_started_at = null,
    run_finished_at = null,
    resource_refs = [],   // default [] for nodes persisted before this field
    image_refs = [],      // inline image chips in the body (IC's mention tokens)
    negative_body,         // absent = no negative prompt; '' = cleared but keep the box (Phase 2 asset library)
    last_dropped,         // knobs the backend ignored on the last run (P4)
    last_dropped_refs,    // references it could not use on that run (P4 assets)
    mentioned_assets = [], // assets named in the body with @ (inline chips)
    last_mention_dropped,  // what a mentioned asset's bundle would not send
    last_mention_error = null, // that bundle request itself failed
    gen = null,           // absent = legacy text prompt
  } = data as unknown as PromptNodeData;
  const { t } = useTranslation();
  // The "Ignored" badge carries BOTH ledgers the last run reported: knobs the
  // provider could not honour, and references it could not use. They are
  // orthogonal (a run can lose either or both), so they are stored apart and
  // only joined here, at the one place a user reads them. References are
  // grouped by reason rather than listed per-url: the badge is a summary, and
  // the urls are in the tooltip.
  // BOTH reference ledgers, joined only here. `last_dropped_refs` is what the
  // BACKEND could not use on the run; `last_mention_dropped` is what the bundle
  // endpoint refused to send for an @-mentioned asset at dispatch. Different
  // authorities, same question for the user ("which picture is missing"), so
  // they share one badge — and both are rewritten by every run, so neither can
  // describe an older one.
  const droppedRefs: DroppedRef[] = useMemo(
    () => [
      ...(Array.isArray(last_dropped_refs) ? last_dropped_refs : []),
      ...(Array.isArray(last_mention_dropped) ? last_mention_dropped : []),
    ],
    [last_dropped_refs, last_mention_dropped],
  );
  const ignoredParts: string[] = useMemo(() => {
    const refCounts = droppedRefs.reduce<Map<string, number>>((acc, ref) => {
      const reason = String(ref?.reason || 'unresolved');
      return acc.set(reason, (acc.get(reason) ?? 0) + 1);
    }, new Map());
    return [
      ...(Array.isArray(last_dropped) ? last_dropped : []),
      ...[...refCounts.entries()].map(([reason, count]) =>
        t('canvas.ignoredRefs', {
          count,
          // An unrecognised code still renders as itself: a badge that omits a
          // reference because nobody wrote its label is the silent drop again.
          reason: t(`canvas.refDropReason.${reason}`, reason),
        }),
      ),
    ];
  }, [droppedRefs, last_dropped, t]);
  // BOTH halves of the tooltip, always — not one or the other.
  //
  // It used to pick the URL list when any reference was dropped and the knob
  // sentence otherwise, so a run that lost a knob AND a reference showed only
  // the URLs and "why was quality ignored" became unreachable. The badge joins
  // two orthogonal ledgers; its tooltip has to as well, or the badge says
  // "Ignored: quality, 1 reference" and can only explain one of them.
  const ignoredTitle = useMemo(() => {
    const lines: string[] = [];
    if (Array.isArray(last_dropped) && last_dropped.length > 0) {
      lines.push(t('canvas.knobNotSupported'));
    }
    lines.push(...droppedRefs.map((r) => `${r.url} — ${r.reason}`));
    return lines.join('\n');
  }, [droppedRefs, last_dropped, t]);
  const patch = useNodeDataPatch(id);
  // Read-only: every control on this node writes — the body/negative text,
  // the kind/model/agent/ratio/count pickers, the @-ref chips' remove
  // button, the library loader (it mints a media node + connection) and
  // Retry (re-dispatches the run). Nothing here is view-only.
  //
  // The two TEXT areas take `readOnly`, not `disabled`: a prompt body is
  // exactly the kind of thing a viewer opens the canvas to read and copy,
  // and a disabled textarea can't be focused or selected. Everything else
  // here is a picker or a button, which has no text worth copying and no
  // `readonly` semantics in HTML — those stay `disabled`.
  const readOnly = useCanvasReadOnly();
  const genKind = gen?.kind ?? 'text';
  const genModels = useGenerationModels(gen ? gen.kind : undefined);
  // Text-mode model options come from the platform DB catalog (same source as
  // genModels), never a hardcoded list (P0-1).
  const textModels = useTextModels();
  // Agent picker (CC3) — writes the pre-plumbed agent_id channel; the run
  // injects the agent's IDENTITY/SOUL server-side.
  const agents = useAgents();

  // Library picker (Phase 2 asset library) — pulls a saved prompt + its
  // cover into this node, wiring a fresh media node upstream of it.
  const [libraryOpen, setLibraryOpen] = useState(false);

  // Smart nodes keep the tone's border colour but drop the whole-card
  // animate-pulse — the status badge's dot carries the motion (P1-5).
  const haloTone = RUN_STATUS_TONE[run_status].replace('animate-pulse', '').trim();

  // Run-time pill (P1-2, Infinite's .run-time-pill): live seconds while
  // running, final duration pinned in green once succeeded.
  const running = run_status === 'running';
  const liveElapsed = useElapsedSeconds(running ? run_started_at : null, running);
  const finalElapsed =
    run_status === 'succeeded' && run_started_at && run_finished_at
      ? elapsedSeconds(run_started_at, new Date(run_finished_at).getTime())
      : null;
  const pillText = running
    ? formatElapsed(liveElapsed)
    : finalElapsed !== null
      ? formatElapsed(finalElapsed)
      : null;

  // ── IME-safe draft mirror (2026-08-18 incident) ─────────────────────────
  // The textarea renders a LOCAL draft, not the store body: patching the
  // store on every keystroke round-trips through React Flow asynchronously,
  // and a controlled value rewrite mid-composition breaks the IME (raw
  // pinyin commits as text). While composing nothing is pushed; the store
  // gets the final text once on compositionend. `lastPushedRef` keeps a
  // late-arriving echo of our own patch from stomping newer local input.
  const [draft, setDraft] = useState(body);
  const composingRef = useRef(false);
  const lastPushedRef = useRef(body);
  useEffect(() => {
    if (!composingRef.current && body !== lastPushedRef.current) {
      lastPushedRef.current = body;
      setDraft(body);
    }
  }, [body]);
  const pushBody = useCallback(
    (v: string) => {
      lastPushedRef.current = v;
      setDraft(v);
      patch({ body: v });
    },
    [patch],
  );

  const mention = useCanvasMentionPicker();

  // ── Image chips in the body ──────────────────────────────────────────────
  const bodyEditorRef = useRef<PromptBodyEditorHandle | null>(null);
  const [dropHint, setDropHint] = useState<LibraryDropTarget | null>(null); // A panel drag hovering this node, and what it will become — the hint below says it before the drop resolves.
  /** Body height when a press on the resize grip started — see the resizer. */
  const pressHeightRef = useRef<number | null>(null);
  // Seeded once: the document owns the chips from then on.
  const seededChips = useRef<PromptImageRef[]>(
    (image_refs ?? []) as PromptImageRef[],
  ).current;
  // The name table the editor re-hydrates `@[asset:id]` tokens with. Seeded
  // once for the same reason the image chips are: from then on the document
  // owns the list, and the editor keeps accumulating names it has seen.
  const seededAssetChips = useRef<MentionedAsset[]>(
    (mentioned_assets ?? []) as MentionedAsset[],
  ).current;
  // The document is the chip list; node data mirrors it so a reload can
  // rebuild them (body is plain text and cannot carry them).
  const handleImageRefsChange = useCallback(
    (refs: PromptImageRef[]) => {
      patch({ image_refs: refs });
    },
    [patch],
  );
  // Same rule for asset mentions, and it is what makes "delete the chip,
  // delete the reference" true without any extra bookkeeping: the document is
  // re-collected on every change, so a removed chip prunes the entry here and
  // the run stops bundling that asset.
  const handleAssetRefsChange = useCallback(
    (assets: MentionedAsset[]) => {
      patch({ mentioned_assets: assets });
    },
    [patch],
  );
  // From this node's OWN data — no graph read, so the strip needs no `s.nodes`
  // subscription for the half that belongs to this card. Memoised because the
  // `?? []` fallback would otherwise mint a fresh array every render and defeat
  // the strip's own memo on a node that has no mentions.
  const mentionedAssets = useMemo(
    () => (mentioned_assets ?? []) as MentionedAsset[],
    [mentioned_assets],
  );
  // While the picker is open, Escape, the arrows and Enter belong to it, not
  // the text. The picker never takes focus (the editor must keep it, or its
  // blur closes the popover), so its keys arrive here and are forwarded.
  const mentionPickerRef = useRef<PromptMentionPickerHandle | null>(null);
  const handleBodyKeyDown = useCallback(
    (event: KeyboardEvent): boolean => {
      if (!mention.pickerOpen) return false;
      if (event.key === 'Escape') {
        mention.closePicker();
        return true;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        mentionPickerRef.current?.move(event.key === 'ArrowDown' ? 1 : -1);
        return true;
      }
      if (event.key === 'Tab' && !event.shiftKey && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        mentionPickerRef.current?.cycleTab();
        return true;
      }
      if (event.key === 'Enter') {
        // Only swallow Enter when there was something to insert. An empty
        // result list must let the keystroke reach the text, or the box looks
        // frozen while the popover happens to be open.
        return mentionPickerRef.current?.commitActive() ?? false;
      }
      return false;
    },
    [mention],
  );
  // Wired input images (IC parity ⑤ — Infinite's 「N 输入图」row + the
  // @-picker's 输入图 tab). Recomputed from the live graph so absorbing /
  // rewiring upstream nodes updates the row immediately.
  //
  // These three are SELECTORS over the store, never a subscription to
  // `s.nodes` itself (Wave 1+2 Task 4): a drag tick swaps that array every
  // frame, so subscribing to it re-rendered every prompt card ~60×/s over
  // positions belonging to other nodes. Each hook's output is stable while
  // THIS card's inputs are — see `useGraphDerived.ts`.
  const inputUrls = useNodeInputUrls(id);
  const upstreamText = useUpstreamPromptText(id);
  // IC 一键运行 (canRunSmartCascade): only the cascade tail carries the
  // Run-chain button.
  const chainTail = useIsChainTail(id);
  const chainRunning = useChainRunStore((s) => s.runningTail === id);
  const requestChainStop = useChainRunStore((s) => s.requestStop);
  // The card's heading is the literal "Prompt", so the body's first line is
  // what a user would call this one — that is what the target bar names.
  const openLibraryForRefs = useCallback(() => {
    const title = (body ?? '').split('\n')[0].slice(0, 40) || 'Prompt';
    const target = { nodeId: id, kind: 'prompt' as const, title };
    useLibraryStore.getState().openPanel({ page: 'media', mediaStore: 'uploads', focusSearch: true, target });
  }, [id, body]);
  const manualRefs = (data as unknown as PromptNodeData).manual_refs ?? [];
  const manualUrlSet = new Set(manualRefs.map((r) => r.url));
  const removeManualRef = useCallback(
    (url: string) => {
      const current =
        ((useCanvasCoreStore
          .getState()
          .nodes.find((n) => (n as { id?: unknown }).id === id) as
          | { data?: PromptNodeData }
          | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
      patch({ manual_refs: current.filter((r) => r.url !== url) });
    },
    [id, patch],
  );

  // IC reorderManualInputRefs — drag one manual thumb onto another; the
  // left/right half of the drop target picks before/after.
  const reorderManualRefs = useCallback(
    (fromUrl: string, toUrl: string, before: boolean) => {
      const current = ((useCanvasCoreStore
        .getState()
        .nodes.find((n) => (n as { id?: unknown }).id === id) as
        | { data?: PromptNodeData }
        | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
      const next = reorderRefs(current, fromUrl, toUrl, before);
      if (next !== current) patch({ manual_refs: next });
    },
    [id, patch],
  );
  // Read source_ref from the store (not props): patches land there first,
  // so the toggle highlight is correct even before React Flow re-renders
  // the node with fresh data.
  const sourceRef = useCanvasCoreStore(
    (s) =>
      ((s.nodes.find((n) => (n as { id?: unknown }).id === id) as
        | { data?: PromptNodeData }
        | undefined)?.data ?? {}).source_ref,
  );
  const toggleSourceRef = useCallback(
    (url: string) => {
      patch({ source_ref: sourceRef === url ? undefined : url });
    },
    [patch, sourceRef],
  );

  // Scope for the asset calls the mention picker makes. '' when the canvas URL
  // has no team segment — the picker says so rather than sending a request
  // that is a 403 by construction.
  const { scopeId } = useCanvasScope();
  const canvasId = useCanvasCoreStore((s) => s.canvasId);

  // The provider's reference ceiling, for greying the inputs past it. null =
  // unknown (loading / no model / old backend), which renders FULL support.
  const caps = useModelCapabilities(gen?.model ?? null);
  const maxRefs = caps?.max_refs ?? null;

  // The strip, in the order the run delivers — mentions (asset references)
  // first, then the wired images, with the connected asset cards' references
  // counted ahead of both. `promptStrip.ts` owns the arithmetic AND the cases
  // where it refuses to answer; this component only draws what it returns.
  //
  // Both graph-derived inputs come through SELECTOR HOOKS, never `s.nodes`:
  // this card must not re-render when an unrelated node is dragged, which is
  // what `CanvasSurface.rerender.test.tsx` pins. `inputUrls` is shallow-stable
  // and the card contribution is a number, so a drag changes neither.
  const assetRefsAhead = useUpstreamAssetRefCount(id, maxRefs);
  const stripEntries = useMemo(
    () =>
      promptStripEntries({
        mentions: mentionedAssets,
        inputUrls,
        assetRefsAhead,
        maxRefs,
      }),
    [mentionedAssets, inputUrls, assetRefsAhead, maxRefs],
  );

  /** The `@Image N` candidates: every durable input this node already has.
   *  Numbered by their position among the WIRED images, which is what the
   *  `@Image N` alias has always meant — not the delivery position, which
   *  moves as assets are added and would rewrite chips already in the text. */
  const mentionImages = useMemo(
    () => inputUrls.map((url, i) => ({ url, label: `Image ${i + 1}` })),
    [inputUrls],
  );

  const handleMentionImage = useCallback(
    (image: { url: string; label: string }) => {
      bodyEditorRef.current?.insertImage({
        url: image.url,
        alias: image.label,
        kind: 'image',
      });
      patch({ source_ref: image.url });
      mention.closePicker();
    },
    [patch, mention],
  );

  const handleMentionAsset = useCallback(
    async (asset: AssetSummary) => {
      // Close FIRST: the detail fetch below is an interactive gap, and leaving
      // the rows up would let a second click start a second insert.
      mention.closePicker();
      // The asset's primary-slot files, for the reference strip only. The RUN
      // fetches this again at dispatch (a stale snapshot would deliver the
      // wrong files); what it buys here is that one mention's span on the
      // strip is a known number instead of a guess.
      //
      // A failure does NOT block the mention — the chip is fully functional
      // without it and the run re-asks. It is logged, and the strip renders
      // the mention as "span unknown" rather than inventing a count.
      let refIds: string[] | undefined;
      try {
        const detail = await fetchAssetDetail(scopeId, asset.id);
        refIds = primarySlotFileIds(detail, null);
      } catch (err) {
        console.error('[PromptNodeView] mention detail fetch failed:', err);
      }
      bodyEditorRef.current?.insertAsset({
        asset_id: asset.id,
        name: asset.name,
        asset_type: asset.asset_type,
        cover_file_id: asset.cover_file_id,
        ...(refIds ? { ref_resource_ids: refIds } : {}),
      });
    },
    [mention, scopeId],
  );

  // ── Library picker handler ───────────────────────────────────────────────
  // Applies the pure-function result: patch this node's body/negative_body,
  // append the new media node, and wire it in as a source connection.
  const handlePickAsset = useCallback(
    async (asset: PromptAsset, lang: 'en' | 'zh') => {
      // Close the picker FIRST: the mint await below leaves an interactive
      // window — with the picker still open, rapid clicks on rows would fire
      // concurrent handlePickAsset runs (duplicate POSTs + duplicate node
      // pairs). Unmounting the rows up front removes the window entirely.
      setLibraryOpen(false);
      // Mint the durable URL BEFORE reading getState() below — the await
      // here is the only async gap in this handler, so grabbing the store
      // snapshot after it (not before) ensures we build on top of whatever
      // other canvas mutations landed while the mint was in flight (M3).
      let mediaUrl: string;
      let mediaKind: 'image' | 'video';
      try {
        const imported = await importResourceAsCanvasMedia(asset.id);
        mediaUrl = imported.url;
        mediaKind = imported.kind;
      } catch (err) {
        console.error('[promptAsset] durable import failed, falling back to cover:', err);
        mediaUrl = getResourceCoverUrl(asset.id); // visual-only fallback, no i2i
        mediaKind = 'image'; // cover endpoint always serves an image
      }

      // Fresh reads at handler time, not render-time subscriptions — avoids
      // inserting into a stale nodes/connections snapshot when other canvas
      // mutations landed between this node's last render and the click (M3).
      const { nodes, connections, setNodes, setConnections } = useCanvasCoreStore.getState();
      const selfNode = nodes.find((n) => (n as unknown as { id: string }).id === id);
      const promptNodePosition =
        (selfNode as unknown as { position?: { x: number; y: number } } | undefined)?.position ??
        { x: 0, y: 0 };
      const { promptPatch, mediaNode, connection } = buildPromptAssetLoad({
        asset,
        lang,
        promptNodeId: id,
        promptNodePosition,
        mediaUrl,
        mediaKind,
      });
      // One atomic setNodes call — folding the self-patch into the same
      // array write that appends mediaNode avoids the two-write race where
      // patch()'s set(nodes-with-patch) gets clobbered by this handler's own
      // stale `nodes` snapshot (the bug this replaced: patch() landed, then
      // setNodes([...nodes, mediaNode]) overwrote it right back out).
      const nextNodes = nodes.map((n) =>
        (n as unknown as { id: string }).id === id
          ? ({
              ...n,
              data: { ...(n as unknown as { data?: object }).data, ...promptPatch },
            } as unknown as CanvasNode)
          : n,
      );
      setNodes([...nextNodes, mediaNode as unknown as CanvasNode]);
      setConnections([...connections, connection as unknown as CanvasConnection]);
    },
    [id],
  );

  // ────────────────────────────────────────────────────────────────────────

  return (
    <div
      data-testid="smart-prompt-node"
      className={`group relative mh-node ${haloTone} ${selected ? 'mh-node-selected' : ''}`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.prompt }}
      onDragOver={(e) => { if (readOnly || !hasLibraryDrag(e.dataTransfer)) return; e.preventDefault(); setDropHint({ kind: 'prompt', nodeId: id, mention: e.altKey }); }}
      onDragLeave={() => setDropHint(null)}
      onDrop={(e) => {
        if (readOnly || !hasLibraryDrag(e.dataTransfer)) return;
        e.preventDefault(); e.stopPropagation(); setDropHint(null);
        const items = readLibraryDrag(e.dataTransfer) ?? [];
        if (e.altKey) { for (const it of items) bodyEditorRef.current?.insertText(`@${it.title} `); return; } // ⌥ = MENTION, an edit to the prompt DOCUMENT: only this component holds the editor handle, so `dropLibraryItems` routes it and the insert happens here. Plain text for now — chips are P3's.
        void dropLibraryItems(items, { kind: 'prompt', nodeId: id, mention: false }, scopeId, { maxRefs: maxRefs ?? MAX_REFERENCE_IMAGES });
      }}
    >
      {dropHint && <span data-testid="prompt-drop-hint" className="pointer-events-none absolute inset-0 z-20 flex items-start justify-center rounded-[inherit] border-2 border-[var(--accent-border)] bg-[var(--accent-soft)] pt-1 text-[10px] font-medium text-[var(--accent-text)]">{t(dropConsequenceKey(dropHint))}</span>}
      <Handle
        type="target"
        position={Position.Left}
      />
      <NodeDeleteButton nodeId={id} readOnly={readOnly} />
      <div className="mh-node-head">
        <div className="mh-node-title">Prompt</div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1.5">
          {/* Text stays the legacy LLM path; Image/Video route Run through
              the G4-B1 generation tasks (Infinite composer's kind toggle). */}
          <UiSelect
            triggerClassName="nodrag mh-chip focus-visible:ring-1 focus-visible:ring-canvas-strong/40"
            value={genKind}
            onChange={(e) => {
              const next = e.target.value;
              if (next === 'text') {
                patch({ gen: null });
              } else {
                const settings: PromptGenSettings =
                  next === 'image'
                    ? { kind: 'image', model: gen?.model ?? '', ratio: gen?.ratio ?? '1:1', count: gen?.count ?? 1 }
                    : { kind: 'video', model: gen?.model ?? '', aspect: gen?.aspect ?? '16:9' };
                patch({ gen: settings });
              }
            }}
            aria-label="Prompt kind"
            disabled={readOnly}
          >
            <option value="text">Text</option>
            <option value="image">Image</option>
            <option value="video">Video</option>
          </UiSelect>
          <button
            type="button"
            data-testid="prompt-split-toggle"
            title="Split the prompt on a separator — each item runs as its own generation"
            onClick={() => patch({ split_enabled: !split_enabled })}
            disabled={readOnly}
            className={`${CANVAS_PILL_TRIGGER} flex items-center justify-center gap-1 disabled:cursor-not-allowed disabled:opacity-50 ${
              split_enabled ? 'font-bold text-canvas-text' : ''
            }`}
          >
            <Split size={11} />
          </button>
          <button
            type="button"
            className={`${CANVAS_PILL_TRIGGER} flex items-center justify-center disabled:cursor-not-allowed disabled:opacity-50`}
            onClick={() => setLibraryOpen(true)}
            aria-label={t('canvas.library.promptTemplates', 'Prompt Templates')}
            data-testid="prompt-library-button"
            disabled={readOnly}
          >
            <Library size={12} />
          </button>
          {pillText && (
            <span
              data-testid="prompt-elapsed"
              className={`rounded-full px-1.5 py-0.5 font-mono text-[10px] tabular-nums ${
                running
                  ? 'mh-accent-chip'
                  : 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
              }`}
            >
              {pillText}
            </span>
          )}
          <RunStatusBadge status={run_status} />
          {/* What the backend could not honour on that run (P4). Beside the
              run badge because dispatch is where the dropping happened —
              the pre-run half of the same loop is the footer's stranded
              ratio mark. Absent/empty says nothing: silence here means the
              request was honoured, so it may never be a default. */}
          {ignoredParts.length > 0 && (
            <span
              data-testid="dropped-knobs-badge"
              title={ignoredTitle}
              className="rounded-full bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn"
            >
              {t('canvas.ignoredKnobs', { knobs: ignoredParts.join(', ') })}
            </span>
          )}
        </div>
      </div>
      {/* relative so PromptMentionPicker's `top-full` positions against this section */}
      <div className="relative p-3">
        {/* Upstream prompt preview (IC inputPromptPreview). */}
        {upstreamText && (
          <div
            data-testid="prompt-upstream-preview"
            className="mb-1.5 rounded-lg border border-dashed border-canvas-line/70 px-2 py-1"
          >
            <div className="text-[9px] font-bold uppercase tracking-wider text-canvas-muted">
              Upstream
            </div>
            <div className="truncate text-[10px] text-canvas-muted">
              {upstreamText}
            </div>
          </div>
        )}
        {stripEntries.length > 0 && (
          <div
            data-testid="prompt-input-row"
            className="mb-1.5 flex flex-wrap items-center gap-1.5"
          >
            {/* DELIVERY ORDER, not drawing convenience: `generationRunner`
                builds `[...asset references, ...wired images]`, so mentions
                come FIRST here and the position badges count the wired asset
                cards' references ahead of both. The strip is a claim about
                which references survive the provider's ceiling — saying it
                backwards is the confidently-wrong badge this repo has paid
                for before. `promptStrip.ts` owns the arithmetic and the
                cases where it declines to answer. */}
            {stripEntries.map((entry) =>
              entry.kind === 'mention' ? (
                (() => {
                  const asset = entry.asset;
                  const Icon = ASSET_TYPE_ICON[asset.asset_type] ?? ASSET_TYPE_ICON.prop;
                  return (
                    <span
                      key={`mention-${asset.asset_id}`}
                      data-testid="prompt-mention-thumb"
                      data-asset-id={asset.asset_id}
                      data-position={entry.position ?? ''}
                      data-ref-count={entry.refCount ?? ''}
                      data-beyond-limit={entry.beyondLimit ? 'true' : 'false'}
                      title={
                        entry.beyondLimit
                          ? t('canvas.asset.refsLimit', {
                              count: maxRefs ?? 0,
                              defaultValue:
                                'This model takes fewer reference images. The rest are not sent.',
                            })
                          : entry.refCount === null
                            ? t('canvas.mention.spanUnknown', {
                                name: asset.name,
                                defaultValue:
                                  '{{name}} — how many reference images it adds is not known yet',
                              })
                            : t('canvas.mention.spanKnown', {
                                name: asset.name,
                                // Pluralised: `spanKnown_one` / `spanKnown_other`
                                // in both locales. A single form would read
                                // "1 reference images".
                                count: entry.refCount,
                                defaultValue_one: '{{name}} — {{count}} reference image',
                                defaultValue_other: '{{name}} — {{count}} reference images',
                                // The un-suffixed fallback too: it is what a
                                // resolver with no plural rules lands on, and
                                // leaving it out shows the raw key there.
                                defaultValue: '{{name}} — {{count}} reference images',
                              })
                      }
                      className={`relative inline-flex h-6 w-6 shrink-0 items-center justify-center overflow-hidden rounded border border-canvas-line/60 text-canvas-muted ${
                        entry.beyondLimit ? 'opacity-40' : ''
                      }`}
                    >
                      {asset.cover_file_id ? (
                        <img
                          src={mediaSrc(getResourceCoverUrl(asset.cover_file_id))}
                          alt={asset.name}
                          className="h-full w-full object-cover"
                        />
                      ) : (
                        <Icon size={11} />
                      )}
                      {/* No badge when the position is unknown: a number would
                          be a claim about the request that nothing supports. */}
                      {entry.position !== null && (
                        <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-canvas-strong px-1 text-[8px] font-bold leading-3 text-canvas-card">
                          {entry.position}
                        </span>
                      )}
                    </span>
                  );
                })()
              ) : (
                <span key={`input-${entry.url}`} className="relative inline-flex">
                  <button
                    type="button"
                    data-testid="prompt-input-thumb"
                    data-position={entry.position ?? ''}
                    data-beyond-limit={entry.beyondLimit ? 'true' : 'false'}
                    title={sourceRef === entry.url ? 'Selected as source' : 'Use as source'}
                    onClick={() => toggleSourceRef(entry.url)}
                    draggable={!readOnly && manualUrlSet.has(entry.url)}
                    onDragStart={(e) => {
                      e.dataTransfer.setData('application/x-nous-ref', entry.url);
                      e.dataTransfer.effectAllowed = 'move';
                    }}
                    onDragOver={(e) => {
                      if (e.dataTransfer.types.includes('application/x-nous-ref'))
                        e.preventDefault();
                    }}
                    onDrop={(e) => {
                      const from = e.dataTransfer.getData('application/x-nous-ref');
                      if (!from || from === entry.url) return;
                      e.preventDefault();
                      const rect = e.currentTarget.getBoundingClientRect();
                      const before = e.clientX < rect.left + rect.width / 2;
                      reorderManualRefs(from, entry.url, before);
                    }}
                    disabled={readOnly}
                    className={`nodrag relative h-6 w-6 shrink-0 overflow-hidden rounded border ${
                      sourceRef === entry.url
                        ? 'border-canvas-strong ring-1 ring-canvas-strong'
                        : 'border-canvas-line/60'
                    } ${entry.beyondLimit ? 'opacity-40' : ''}`}
                  >
                    <img
                      src={mediaSrc(entry.url)}
                      alt={entry.position !== null ? `Input ${entry.position}` : 'Input'}
                      className="h-full w-full object-cover"
                    />
                    {/* IC 图N corner badge */}
                    {entry.position !== null && (
                      <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-canvas-strong px-1 text-[8px] font-bold leading-3 text-canvas-card">
                        {entry.position}
                      </span>
                    )}
                  </button>
                  {/* Manual refs are removable (IC input-thumb-remove). */}
                  {!readOnly && manualUrlSet.has(entry.url) && (
                    <button
                      type="button"
                      data-testid="remove-reference"
                      aria-label="Remove reference"
                      onClick={() => removeManualRef(entry.url)}
                      className="nodrag absolute -right-1 -top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-canvas-strong text-[8px] font-bold text-canvas-card"
                    >
                      ×
                    </button>
                  )}
                </span>
              ),
            )}
            <span className="text-[10px] font-semibold text-canvas-muted">
              {stripEntries.length} inputs
            </span>
            {last_mention_error && (
              <span
                data-testid="mention-bundle-error"
                title={last_mention_error}
                className="rounded-full bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn"
              >
                {t(
                  'canvas.mention.bundleFailed',
                  'A mentioned asset could not be read on the last run',
                )}
              </span>
            )}
            {!readOnly && (
              <button
                type="button"
                data-testid="add-reference"
                aria-label="Add reference image"
                onClick={openLibraryForRefs}
                disabled={stripEntries.length >= MAX_REFERENCE_IMAGES}
                title={
                  stripEntries.length >= MAX_REFERENCE_IMAGES
                    ? `Reference limit reached (${MAX_REFERENCE_IMAGES})`
                    : 'Add reference image'
                }
                className="nodrag ml-auto flex h-6 w-6 shrink-0 items-center justify-center rounded border border-canvas-line text-canvas-muted hover:text-canvas-text disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ImagePlus size={12} />
              </button>
            )}
          </div>
        )}
        {stripEntries.length === 0 && gen && !readOnly && (
          <div className="mb-1.5 flex items-center">
            <button
              type="button"
              data-testid="add-reference"
              aria-label="Add reference image"
              title="Add reference image"
              onClick={openLibraryForRefs}
              className="nodrag flex h-6 items-center gap-1 rounded border border-dashed border-canvas-line px-2 text-[10px] text-canvas-muted hover:text-canvas-text"
            >
              <ImagePlus size={12} />
              Add reference
            </button>
          </div>
        )}
        {/* The resizer now lives on this wrapper. A <textarea> had one for
            free; a contenteditable does not, and IC's promptH (a body height
            the user drags and we persist) is a real behaviour to keep — CSS
            `resize` works on any block box, so the handle moves out here and
            the editor fills it. */}
        <div
          data-testid="prompt-body-resizer"
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y overflow-auto"
          style={body_h ? { height: body_h } : undefined}
          // IC promptH: persist the height the user DRAGS the box to.
          //
          // Persist only when the height actually changed during this press.
          // Comparing against the stored `body_h` instead made every plain
          // click rewrite the height, because the measurement and the stored
          // value are in different units: the canvas scales its surface with
          // a CSS transform, so `getBoundingClientRect()` reports SCALED
          // pixels while `style.height` is written unscaled. At 75% zoom five
          // clicks walked a node from 417px down to 188px. `offsetHeight` is
          // layout pixels (transform-independent), and a before/after
          // comparison needs no agreement with the stored value at all —
          // a click leaves the height untouched, so nothing is written.
          onMouseDown={(e) => {
            // Only a press that STARTS on the resize grip counts. The grip is
            // the bottom-right corner the browser draws for `resize-y`;
            // anywhere else is ordinary text interaction, and focusing the
            // editor can itself shift the box a little — which is why a
            // before/after comparison alone still let clicks rewrite the
            // height.
            const r = e.currentTarget.getBoundingClientRect();
            const onGrip = e.clientX >= r.right - GRIP_PX && e.clientY >= r.bottom - GRIP_PX;
            pressHeightRef.current = onGrip ? e.currentTarget.offsetHeight : null;
          }}
          onMouseUp={(e) => {
            const before = pressHeightRef.current;
            pressHeightRef.current = null;
            if (before === null) return;
            const h = Math.round(e.currentTarget.offsetHeight);
            if (h > 0 && h !== Math.round(before)) patch({ body_h: h });
          }}
        >
          <PromptBodyEditor
            ref={bodyEditorRef}
            value={draft}
            onChange={pushBody}
            onRefsChange={handleImageRefsChange}
            onAssetRefsChange={handleAssetRefsChange}
            knownAssets={seededAssetChips}
            onAtTyped={mention.openPicker}
            onMentionQueryChange={mention.setMentionQuery}
            onKeyDown={handleBodyKeyDown}
            initialChips={seededChips}
            readOnly={readOnly}
          />
        </div>

        {mention.pickerOpen && (
          <PromptMentionPicker
            ref={mentionPickerRef}
            scopeId={scopeId}
            inputImages={mentionImages}
            onPickImage={handleMentionImage}
            onPickAsset={handleMentionAsset}
            onPickLibraryImage={(item) =>
              // Ceiling handed down; close only once something actually landed.
              addReferences(id, [item], scopeId, { maxRefs: maxRefs ?? MAX_REFERENCE_IMAGES }).then(
                (r) => { if (r.added > 0) mention.closePicker(); return r; },
              )
            }
            query={mention.query}
            canvasId={canvasId}
          />
        )}

        {/* Fixed full-screen modal — no relative positioning needed. */}
        {libraryOpen && (
          <AssetPromptPicker onPick={handlePickAsset} onClose={() => setLibraryOpen(false)} />
        )}

        {negative_body !== undefined ? (
          <div className="mt-1.5">
            <span className="text-[10px] uppercase tracking-wider text-rose-400/85">
              Negative
            </span>
            <textarea
              value={negative_body ?? ''}
              onChange={(e) => patch({ negative_body: e.target.value })}
              placeholder="Negative prompt"
              aria-label="Negative prompt"
              rows={2}
              readOnly={readOnly}
              className="nodrag nowheel mt-0.5 w-full resize-y rounded-lg border border-rose-400/25 bg-rose-500/[.06] px-2 py-1 text-[11px] text-ink-300 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-rose-400/40 read-only:opacity-80 read-only:cursor-default"
            />
          </div>
        ) : null}

        {/* Footer wraps: pills + Run no longer crush each other on a
            narrow card (2026-08-22 "太拥挤"). */}
        <div className="mt-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5 text-xs">
          {!gen && (
            <div className="flex flex-1 items-center gap-1.5">
              <UiSelect
                triggerClassName={CANVAS_PILL_TRIGGER}
                className="min-w-0 flex-1 truncate"
                value={provider_slug}
                onChange={(e) => patch({ provider_slug: e.target.value })}
                aria-label="Prompt provider"
                disabled={readOnly}
              >
                {/* Empty value → backend resolves the DB catalog default. */}
                <option value="">Catalog default</option>
                {textModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.display_name || m.name}
                  </option>
                ))}
              </UiSelect>
              <UiSelect
                triggerClassName={CANVAS_PILL_TRIGGER}
                className="min-w-0 flex-1 truncate"
                value={agent_id ?? ''}
                onChange={(e) => patch({ agent_id: e.target.value || null })}
                aria-label="Prompt agent"
                disabled={readOnly}
              >
                {/* Empty → plain runner (no persona injected). */}
                <option value="">No agent</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </UiSelect>
            </div>
          )}
          {gen && (
            <div className="flex min-w-0 flex-1 basis-full items-center sm:basis-auto">
              <GenFooterControls
                gen={gen}
                models={genModels}
                onChange={(g) => patch({ gen: { ...gen, ...g } })}
                disabled={readOnly}
              />
            </div>
          )}
          {/* In-node Run (Infinite parity: footer 右端的深色「运行」药丸) —
              same single-prompt runner the failure panel's Retry uses; the
              regen store's per-prompt lock makes double-dispatch a no-op. */}
          <button
            type="button"
            onClick={() => void rerunPrompt(id)}
            disabled={readOnly || run_status === 'running'}
            data-testid="prompt-node-run"
            aria-label="Run prompt"
            className="nodrag ml-auto flex shrink-0 items-center gap-1 rounded-full border border-transparent bg-canvas-strong px-3 py-0.5 text-xs font-bold text-canvas-card hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Play size={11} />
            Run
          </button>
          {chainTail && (
            <button
              type="button"
              onClick={() =>
                chainRunning ? requestChainStop() : void startChainRun(id)
              }
              disabled={readOnly}
              data-testid="prompt-node-run-chain"
              aria-label={chainRunning ? 'Stop chain' : 'Run chain'}
              title={
                chainRunning
                  ? 'Stop after the current prompt'
                  : 'Run the whole upstream chain'
              }
              className={`nodrag flex shrink-0 items-center gap-1 rounded-full border px-3 py-0.5 text-xs font-bold disabled:cursor-not-allowed disabled:opacity-40 ${
                chainRunning
                  ? 'border-rose-400/60 text-rose-500'
                  : 'border-canvas-strong text-canvas-text hover:opacity-80'
              }`}
            >
              {chainRunning ? <Square size={11} /> : <Zap size={11} />}
              {chainRunning ? 'Stop' : 'Chain'}
            </button>
          )}
        </div>

        {split_enabled && (
          <div
            data-testid="prompt-split-row"
            className="mt-1.5 flex items-center gap-1.5 text-[10px] text-canvas-muted"
          >
            <span>Separator</span>
            <input
              type="text"
              maxLength={8}
              value={split_separator ?? ';'}
              onChange={(e) => patch({ split_separator: e.target.value })}
              aria-label="Split separator"
              className="nodrag w-14 rounded border border-canvas-line bg-transparent px-1.5 py-0.5 text-xs text-canvas-text outline-none"
            />
            <span data-testid="prompt-split-count" className="font-semibold">
              {splitPromptItems(draft, (split_separator ?? ';')).length} prompts
            </span>
          </div>
        )}
        {/* Ref chips: show attached resources below the textarea */}
        {(resource_refs as PromptResourceRef[]).length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1" data-testid="prompt-ref-chips">
            {(resource_refs as PromptResourceRef[]).map((ref) => (
              <span
                key={ref.resource_id}
                className="mh-accent-chip inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px]"
                data-testid="prompt-ref-chip"
              >
                @{ref.name}
                <button
                  className="ml-0.5 opacity-70 hover:opacity-100 disabled:cursor-not-allowed disabled:opacity-40"
                  aria-label={`Remove reference to ${ref.name}`}
                  disabled={readOnly}
                  onMouseDown={(e) => {
                    // prevent blur from firing before the click is processed
                    e.preventDefault();
                    const current = resource_refs as PromptResourceRef[];
                    patch({
                      resource_refs: current.filter(
                        (r) => r.resource_id !== ref.resource_id,
                      ),
                    });
                  }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Failed-run recovery panel (P3-B): a full-width rose panel with the
            error text + Retry, replacing the old truncated one-liner + a tiny
            header chip. Mirrors the OutputNodeView RecoverCell shape. */}
        {run_status === 'failed' && (
          <div
            data-testid="prompt-failure-panel"
            role="alert"
            className="mt-2 rounded-lg border border-rose-400/50 bg-rose-400/10 p-2"
          >
            <div className="text-[11px] font-bold text-rose-500">Run failed</div>
            {run_error && (
              <div className="mt-0.5 text-[10px] text-canvas-muted" title={run_error}>
                {run_error}
              </div>
            )}
            <button
              type="button"
              onClick={() => void rerunPrompt(id)}
              disabled={readOnly}
              data-testid="prompt-retry"
              className="nodrag mh-chip mt-1.5 !border-rose-400/60 !text-rose-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Retry
            </button>
          </div>
        )}
      </div>
      <Handle
        type="source"
        position={Position.Right}
      />
    </div>
  );
}
