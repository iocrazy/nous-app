import { mediaSrc } from '../mediaUrl';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';

import { NodeDeleteButton } from './NodeDeleteButton';
import { ImagePlus, Library, Play, Split, Square, Zap } from 'lucide-react';

import type { CanvasConnection, CanvasNode } from '../../types';
import type { GeneratedImageRef, PromptGenSettings, PromptNodeData, PromptResourceRef } from '../types';
import { RUN_STATUS_TONE, SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useGenerationModels } from './useGenerationModels';
import { useTextModels } from './useTextModels';
import { useAgents } from './useAgents';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';
import { isChainTail, startChainRun, useChainRunStore } from '../chainRun';
import { splitPromptItems } from '../promptSplit';
import { rerunPrompt } from '../regenerate';
import { resolveSourceUrls, upstreamPromptText } from '../promptInputs';
import { MAX_REFERENCE_IMAGES, reorderRefs } from '../refOrder';
import { RunStatusBadge } from './RunStatusBadge';
import {
  elapsedSeconds,
  formatElapsed,
  useElapsedSeconds,
} from './elapsed';
import { useCanvasMentionPicker } from './useCanvasMentionPicker';
import { CanvasMentionPicker } from './CanvasMentionPicker';
import { GenFooterControls } from './GenFooterControls';
import { AssetPromptPicker } from './AssetPromptPicker';
import { buildPromptAssetLoad } from '../loadPromptAsset';
import { importResourceAsCanvasMedia } from '../mediaImport';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { useResourceSearch } from '../../../../hooks/useResourceSearch';
import type { ResourceSearchResult } from '../../../../types';
import { getResourceCoverUrl, type PromptAsset } from '../../../../services/resourceService';
import { ASPECT_RATIOS } from '../aspectPresets';
import { UiSelect } from '../../../../components/ui';

type ActiveKind = '' | 'video' | 'image' | 'doc' | 'audio' | 'pdf';

// Canvas pill trigger — keeps the node's ghost/rounded look while borrowing the
// shared UiSelect portal menu (fixes the native popup covering the trigger).
import { CANVAS_PILL_TRIGGER } from './canvasPill';

export function PromptNodeView({ id, data, selected }: NodeProps) {
  const {
    body,
    split_enabled,
    split_separator,
    provider_slug,
    agent_id = null,
    run_status,
    run_error,
    run_started_at = null,
    run_finished_at = null,
    resource_refs = [],   // default [] for nodes persisted before this field
    negative_body,         // absent = no negative prompt; '' = cleared but keep the box (Phase 2 asset library)
    gen = null,           // absent = legacy text prompt
  } = data as unknown as PromptNodeData;
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

  // Kind filter for the @-mention picker tabs (All / Video / Image / Doc …)
  const [activeKind, setActiveKind] = useState<ActiveKind>('');

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

  // ── @-mention handler ────────────────────────────────────────────────────
  // Builds a PromptResourceRef from the picked SearchResult and appends it
  // to resource_refs (deduplicated by resource_id).
  const handleSelectRef = useCallback(
    (item: ResourceSearchResult) => {
      const ref: PromptResourceRef = {
        resource_id: item.id,
        name: item.name,
        kind: item.kind,
        mime: item.mime ?? '',
        scope: item.scope,
      };
      const current = resource_refs as PromptResourceRef[];
      if (current.some((r) => r.resource_id === ref.resource_id)) return;
      patch({ resource_refs: [...current, ref] });
    },
    [resource_refs, patch],
  );

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

  const mention = useCanvasMentionPicker({
    value: draft,
    onValueChange: pushBody,
    onSelectRef: handleSelectRef,
  });

  const handleBodyChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setDraft(e.target.value);
      if (composingRef.current) return;
      mention.handleChange(e);
    },
    [mention.handleChange],
  );
  const handleCompositionStart = useCallback(() => {
    composingRef.current = true;
  }, []);
  const handleCompositionEnd = useCallback(
    (e: React.CompositionEvent<HTMLTextAreaElement>) => {
      composingRef.current = false;
      mention.handleChange({
        target: e.currentTarget,
      } as unknown as React.ChangeEvent<HTMLTextAreaElement>);
    },
    [mention.handleChange],
  );

  // Wired input images (IC parity ⑤ — Infinite's 「N 输入图」row + the
  // @-picker's 输入图 tab). Recomputed from the live graph so absorbing /
  // rewiring upstream nodes updates the row immediately.
  const storeNodes = useCanvasCoreStore((s) => s.nodes);
  const storeConnections = useCanvasCoreStore((s) => s.connections);
  const storeNodes2 = useCanvasCoreStore((s) => s.nodes);
  // IC 一键运行 (canRunSmartCascade): only the cascade tail carries the
  // Run-chain button.
  const chainTail = useMemo(
    () => isChainTail(id, storeNodes2, storeConnections),
    [id, storeNodes2, storeConnections],
  );
  const chainRunning = useChainRunStore((s) => s.runningTail === id);
  const requestChainStop = useChainRunStore((s) => s.requestStop);
  const inputUrls = useMemo(
    () =>
      resolveSourceUrls(
        id,
        storeNodes as CanvasNode[],
        storeConnections as CanvasConnection[],
      ),
    [id, storeNodes, storeConnections],
  );
  // Read source_ref from the store (not props): patches land there first,
  // so the toggle highlight is correct even before React Flow re-renders
  // the node with fresh data.
  const upstreamText = useMemo(
    () =>
      upstreamPromptText(
        id,
        storeNodes as CanvasNode[],
        storeConnections as CanvasConnection[],
      ),
    [id, storeNodes, storeConnections],
  );
  const [refPickerOpen, setRefPickerOpen] = useState(false);
  const manualRefs = (data as unknown as PromptNodeData).manual_refs ?? [];
  const manualUrlSet = new Set(manualRefs.map((r) => r.url));
  const addManualRef = useCallback(
    async (resourceId: string) => {
      try {
        const minted = await importResourceAsCanvasMedia(resourceId);
        const current =
          ((useCanvasCoreStore
            .getState()
            .nodes.find((n) => (n as { id?: unknown }).id === id) as
            | { data?: PromptNodeData }
            | undefined)?.data?.manual_refs ?? []) as GeneratedImageRef[];
        if (current.some((r) => r.url === minted.url)) return;
        patch({ manual_refs: [...current, { url: minted.url, kind: minted.kind }] });
      } catch (err) {
        console.error('[PromptNodeView] add reference failed:', err);
      } finally {
        setRefPickerOpen(false);
      }
    },
    [id, patch],
  );
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
  // IC rule: the picker opens on the Input tab when inputs exist, else
  // falls to the asset library; reset each time the picker opens.
  const [mentionTab, setMentionTab] = useState<'input' | 'library'>('library');

  // Search for resources whenever the picker is open (debounced inside the hook).
  // Pass '' when picker is closed so cached data is reused on next open.
  const { data: searchData, loading: searchLoading } = useResourceSearch(
    mention.pickerOpen ? mention.query : '',
    activeKind,
  );

  // Keep keyboard-wrap bound tight: update the hook's itemCountRef whenever
  // results change. Uses a ref internally so this never triggers re-renders.
  useEffect(() => {
    mention.setItemCount(searchData.results.length);
  }, [searchData.results.length, mention.setItemCount]);

  useEffect(() => {
    if (mention.pickerOpen) {
      setMentionTab(inputUrls.length > 0 ? 'input' : 'library');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on open only
  }, [mention.pickerOpen]);

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
    >
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
            aria-label="Load from library"
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
        </div>
      </div>
      {/* relative so the CanvasMentionPicker's `bottom-full` positions above this section */}
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
        {inputUrls.length > 0 && (
          <div
            data-testid="prompt-input-row"
            className="mb-1.5 flex flex-wrap items-center gap-1.5"
          >
            {inputUrls.map((url, i) => (
              <span key={url} className="relative inline-flex">
                <button
                  type="button"
                  data-testid="prompt-input-thumb"
                  title={sourceRef === url ? 'Selected as source' : 'Use as source'}
                  onClick={() => toggleSourceRef(url)}
                  draggable={!readOnly && manualUrlSet.has(url)}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('application/x-nous-ref', url);
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onDragOver={(e) => {
                    if (e.dataTransfer.types.includes('application/x-nous-ref'))
                      e.preventDefault();
                  }}
                  onDrop={(e) => {
                    const from = e.dataTransfer.getData('application/x-nous-ref');
                    if (!from || from === url) return;
                    e.preventDefault();
                    const rect = e.currentTarget.getBoundingClientRect();
                    const before = e.clientX < rect.left + rect.width / 2;
                    reorderManualRefs(from, url, before);
                  }}
                  disabled={readOnly}
                  className={`nodrag relative h-6 w-6 shrink-0 overflow-hidden rounded border ${
                    sourceRef === url
                      ? 'border-canvas-strong ring-1 ring-canvas-strong'
                      : 'border-canvas-line/60'
                  }`}
                >
                  <img src={mediaSrc(url)} alt={`Input ${i + 1}`} className="h-full w-full object-cover" />
                  {/* IC 图N corner badge */}
                  <span className="pointer-events-none absolute left-0 top-0 rounded-br-md bg-canvas-strong px-1 text-[8px] font-bold leading-3 text-canvas-card">{i + 1}</span>
                </button>
                {/* Manual refs are removable (IC input-thumb-remove). */}
                {!readOnly && manualUrlSet.has(url) && (
                  <button
                    type="button"
                    data-testid="remove-reference"
                    aria-label="Remove reference"
                    onClick={() => removeManualRef(url)}
                    className="nodrag absolute -right-1 -top-1 flex h-3.5 w-3.5 items-center justify-center rounded-full bg-canvas-strong text-[8px] font-bold text-canvas-card"
                  >
                    ×
                  </button>
                )}
              </span>
            ))}
            <span className="text-[10px] font-semibold text-canvas-muted">
              {inputUrls.length} inputs
            </span>
            {!readOnly && (
              <button
                type="button"
                data-testid="add-reference"
                aria-label="Add reference image"
                onClick={() => setRefPickerOpen((v) => !v)}
                disabled={inputUrls.length >= MAX_REFERENCE_IMAGES}
                title={
                  inputUrls.length >= MAX_REFERENCE_IMAGES
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
        {inputUrls.length === 0 && gen && !readOnly && (
          <div className="mb-1.5 flex items-center">
            <button
              type="button"
              data-testid="add-reference"
              aria-label="Add reference image"
              title="Add reference image"
              onClick={() => setRefPickerOpen((v) => !v)}
              className="nodrag flex h-6 items-center gap-1 rounded border border-dashed border-canvas-line px-2 text-[10px] text-canvas-muted hover:text-canvas-text"
            >
              <ImagePlus size={12} />
              Add reference
            </button>
          </div>
        )}
        {refPickerOpen && (
          <div
            data-testid="reference-picker"
            className="mh-pop-in absolute bottom-full left-0 z-50 mb-1"
            onMouseDown={(e) => e.preventDefault()}
          >
            <CanvasMentionPicker
              items={searchData.results}
              query=""
              loading={searchLoading}
              counts={searchData.counts}
              activeKind={activeKind}
              onKindChange={setActiveKind}
              onSelect={(item) => void addManualRef(item.id)}
              activeIndex={0}
            />
          </div>
        )}
        <textarea
          // nodrag → React Flow doesn't start a drag from this input
          // nowheel → wheel events scroll the textarea instead of zooming canvas
          className="nodrag nowheel min-h-[3.5rem] w-full resize-y bg-transparent text-[13px] text-ink-200 outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
          placeholder="What should the model generate? Type @ to reference an asset"
          value={draft}
          onChange={handleBodyChange}
          onCompositionStart={handleCompositionStart}
          onCompositionEnd={handleCompositionEnd}
          onKeyDown={mention.handleKeyDown}
          onBlur={mention.closePicker}
          aria-label="Prompt body"
          rows={3}
          readOnly={readOnly}
        />

        {mention.pickerOpen && (
          <div
            className="mh-pop-in absolute bottom-full left-0 z-50 mb-1"
            onMouseDown={(e) => e.preventDefault()}
          >
            {/* IC's mention-source-tabs: 输入图 / 资产库. */}
            <div className="mb-1 flex items-center gap-1">
              <button
                type="button"
                data-testid="mention-tab-input"
                disabled={inputUrls.length === 0}
                onClick={() => setMentionTab('input')}
                className={`nodrag rounded-full border px-2 py-0.5 text-[10px] font-semibold disabled:cursor-not-allowed disabled:opacity-40 ${
                  mentionTab === 'input'
                    ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
                    : 'border-canvas-line text-canvas-text'
                }`}
              >
                Input images
              </button>
              <button
                type="button"
                data-testid="mention-tab-library"
                onClick={() => setMentionTab('library')}
                className={`nodrag rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                  mentionTab === 'library'
                    ? 'border-canvas-strong bg-canvas-strong text-canvas-card'
                    : 'border-canvas-line text-canvas-text'
                }`}
              >
                Library
              </button>
            </div>
            {mentionTab === 'input' ? (
              <div className="grid grid-cols-4 gap-1.5 rounded-xl border border-canvas-line bg-canvas-card p-2">
                {inputUrls.slice(0, 36).map((url, i) => (
                  <button
                    key={url}
                    type="button"
                    data-testid="mention-input-option"
                    onClick={() => {
                      mention.handleInsertText(`Image ${i + 1}`);
                      patch({ source_ref: url });
                    }}
                    className="nodrag flex flex-col items-center gap-0.5"
                  >
                    <span className="h-12 w-12 overflow-hidden rounded-md border border-canvas-line/60">
                      <img src={mediaSrc(url)} alt={`Image ${i + 1}`} className="h-full w-full object-cover" />
                    </span>
                    <span className="text-[9px] text-canvas-muted">Image {i + 1}</span>
                  </button>
                ))}
              </div>
            ) : (
              <CanvasMentionPicker
                items={searchData.results}
                query={mention.query}
                loading={searchLoading}
                counts={searchData.counts}
                activeKind={activeKind}
                onKindChange={setActiveKind}
                onSelect={mention.handleSelect}
                activeIndex={mention.activeIndex}
              />
            )}
          </div>
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
                  <option key={m.name} value={m.name} data-description={m.actual_provider}>
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
