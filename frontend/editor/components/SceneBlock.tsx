/**
 * SceneBlock — one scene's container + live element editing (spec v3 §3.2/§3.3).
 *
 * Owns exactly one scene's write path via `useSceneSync(scene)` and renders the
 * scene's elements through the TipTap editing surface (`TipTapSceneEditor`).
 * Keystroke semantics (Tab/Shift-Tab type cycle, Enter split, Backspace merge,
 * IME) live in the TipTap keymap; this component owns the surrounding UX — the
 * scene head row, the slash / @-mention / character-cue / transition-preset
 * pickers, the copilot card, and same/cross-scene element drag — feeding it all
 * to the editor through props and reflecting `sync.elements` changes back in via
 * `applyExternalElements`.
 *
 * The scene head row (INT/EXT · location · time) writes through updateSceneMeta,
 * debounced 600ms; text input debounces 500ms inside the editor.
 */
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  // Aliased: a bare `MouseEvent` import SHADOWS the global DOM MouseEvent, which
  // silently broke `document.addEventListener('mousedown', ...)` typing below.
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { applyLocal, buildInverse } from '../opBuilder';
import { createIssue } from '../../services/issuesService';
import { buildOriginId } from '../../components/Todolist/issueOrigin';
import { useOptionalToast } from '../../components/Toast';
import { newElementId, updateSceneMeta } from '../sceneService';
import {
  buildPolishOps,
  requestCopilotOps,
  CopilotDisabledError,
  OpRejectedError,
} from '../copilotService';
import {
  MH_DRAG_MIME,
  type CursorState,
  type ElementOp,
  type ElementType,
  type ScriptElement,
  type SceneDoc,
} from '../types';
import { useSceneSync, type RemoteOpRow } from '../useSceneSync';
import { MentionCombobox, mentionEntries } from './MentionCombobox';
import { SlashMenu, SLASH_ITEMS, filterSlashItems, type SlashItem } from './SlashMenu';
import { CopilotCard, type CopilotPhase } from './CopilotCard';
import { EmptySceneHint } from './EmptyStates';
import { TipTapSceneEditor, type TipTapSceneEditorHandle } from '../tiptap/TipTapSceneEditor';
import type { MenuBridge } from '../tiptap/menuKeymap';
import type { EditorFormat } from '../useEditorState';
import type { SaveState } from '../useSceneSync';
import { ScenePresenceBadge } from '../collab/ScenePresenceBadge';
import type { PresenceUser } from '../collab/useScriptPresence';
import { HeadingSelect } from './HeadingSelect';
import { SceneContextMenu, type SceneContextMenuItem } from './SceneContextMenu';
import { OutputProvenance } from '../../components/agentActivity/OutputProvenance';

/** A scene's save status lifted to the shell for the aggregate SaveIndicator. */
export interface SceneSyncStatus {
  saveState: SaveState;
  resolveConflict: (choice: 'mine' | 'theirs') => void;
}

/**
 * Drag + keyboard scene-reorder wiring, owned by the shell (which knows the full
 * ordered scene list and calls moveScene). One handle drives both paths: HTML5
 * DnD on the `::` grip, and Alt+Arrow while the grip is focused (spec §3.5).
 */
export interface SceneReorderApi {
  /** The scene currently being dragged (for aria-grabbed + drop-target styling). */
  draggingId: string | null;
  /** The active drop target: which scene and which edge the line will sit on. */
  dropTarget: { sceneId: string; edge: 'before' | 'after' } | null;
  onDragStart: (sceneId: string) => void;
  onDragEnd: () => void;
  onDragOver: (sceneId: string, edge: 'before' | 'after') => void;
  onDrop: (sceneId: string, edge: 'before' | 'after') => void;
  onKeyboardMove: (sceneId: string, direction: 'up' | 'down') => void;
  /** Delete a whole scene (heading + all its elements) — from the context menu. */
  onDeleteScene: (sceneId: string) => void;
}

/** An open @-mention / character-cue / transition-preset picker anchored to
 *  one element line. */
interface MentionState {
  elementId: string;
  /** 'inline' = typed `@` inside a line; 'character' = a focused character
   *  cue; 'transition' = a focused transition line (preset picker). */
  kind: 'inline' | 'character' | 'transition';
  query: string;
  /** `centered`: `left` is the anchor's CENTER (see MentionCombobox). */
  position?: { top: number; left: number; centered?: boolean };
}

/** Industry transition presets (laper parity) — offered whenever a transition
 *  line is focused; the line text filters and Enter replaces the whole line. */
const TRANSITION_PRESETS = ['CUT TO:', 'FADE TO:', 'DISSOLVE TO:', 'FADE IN:', 'FADE OUT.'];

/** A toolbar-issued retype of the focused element, routed to the owning scene. */
export interface TypeCommand {
  sceneId: string;
  elementId: string;
  type: ElementType;
  /** Bump to re-fire even when the payload is unchanged. */
  nonce: number;
}

const INT_EXT_OPTIONS = ['INT', 'EXT', 'INT/EXT'];
const TIME_OPTIONS = ['DAY', 'NIGHT', 'DAWN', 'DUSK', 'CONTINUOUS'];

const INPUT_DEBOUNCE_MS = 500;
const META_DEBOUNCE_MS = 600;

interface SceneMeta {
  heading_int_ext: string;
  location_text: string;
  time_of_day: string;
}

export interface SceneBlockProps {
  scene: SceneDoc;
  index: number;
  /** Cumulative block-index offset from `sceneBlockBases` (A1 continuous
   *  numbering): this scene's heading renders as `blockIndexBase + 1` and its
   *  elements as `blockIndexBase + 2, +3, ...`. Defaults to 0 so a bare
   *  SceneBlock (e.g. in isolation tests) still numbers from 1. */
  blockIndexBase?: number;
  /** Reports the focused element up so the toolbar/statistics can track the cursor. */
  onFocusElement?: (cursor: CursorState) => void;
  /** Toolbar retype command targeted (by sceneId) at this block's focused element. */
  typeCommand?: TypeCommand;
  /** Layout engine: 'hollywood' (default) or 'asian'. Both consume the same elements. */
  format?: EditorFormat;
  /** Distinct CAST names for the @-mention / character-cue picker (script-wide). */
  mentionCandidates?: string[];
  /** UPPERCASE character name → rail colour, so a Hollywood character cue can
   *  prefix its name with the SAME colour dot the left rail paints. Forwarded to
   *  TipTapSceneEditor, which resolves each cue's colour into `--cue-dot`. */
  castColors?: Record<string, string>;
  /** Distinct location names (script-wide) for the heading's search-or-create
   *  location picker. */
  locationCandidates?: string[];
  /** Reports this scene's save state up so the shell can aggregate it. */
  onSyncStateChange?: (sceneId: string, status: SceneSyncStatus) => void;
  /** Drag/keyboard reorder wiring (Task 10); absent = reorder disabled. */
  reorder?: SceneReorderApi;
  /** Called when Esc leaves an element line so the shell can clear its
   * `data-editing` styling hook (toolbar emphasis), not any focus/a11y state. */
  onExitEditing?: () => void;
  /** The scene that currently owns the copilot card (shell keeps it to one). */
  copilotActiveSceneId?: string | null;
  /** Notifies the shell which scene (if any) now holds a copilot selection. */
  onCopilotActivate?: (sceneId: string | null) => void;
  /** Reports this scene's live (optimistic) elements up (debounced 1s) so the
   *  shell can derive Statistics / rail entities from in-flight edits. */
  onElementsChange?: (sceneId: string, elements: ScriptElement[]) => void;
  /** Other collaborators (self excluded) currently focused on this scene (P5). */
  focusPresence?: PresenceUser[];
  /** Local user's actor id, so remote self-echoed ops are dropped (C2). */
  selfActorId?: string | null;
  /** Registers this scene's applyRemoteOps with the shell's op-stream router
   *  (called with null on unmount). */
  onRegisterRemoteApply?: (sceneId: string, apply: ((row: RemoteOpRow) => void) | null) => void;
  /** True when a remote op for this scene arrived while it was unmounted
   *  (windowed out); the block refetches its scene on (re)mount to shed the
   *  stale snapshot, then calls onRemoteStaleHandled to clear the flag (C2). */
  remoteStale?: boolean;
  onRemoteStaleHandled?: (sceneId: string) => void;
  /** ── Cross-scene paragraph drag (shell-coordinated) ──
   * The shell holds which element is being dragged from which scene; every
   * OTHER SceneBlock treats that as an external drag it can accept. */
  elementDrag?: { sceneId: string; element: ScriptElement } | null;
  /** Report drag start/finish up so the shell can broadcast the drag. */
  onElementDragBegin?: (sceneId: string, element: ScriptElement) => void;
  onElementDragDone?: () => void;
  /** Ask the shell to delete the dragged element from its SOURCE scene after
   *  this (target) scene inserted it. */
  onCrossSceneDelete?: (sceneId: string, elementId: string) => void;
  /** Registers a dispatcher the shell can use to apply ops to this scene from
   *  outside (the cross-scene delete); called with null on unmount. */
  onRegisterExternalOps?: (sceneId: string, fn: ((ops: ElementOp[]) => void) | null) => void;
  /** Paged mode v2: page seams keyed by element id (rendered before that row). */
  pageSeams?: Map<string, { page: number; filler: number }>;
}

/** Which half of a block the pointer is over → the drop edge. */
function edgeFromPointer(el: HTMLElement, clientY: number): 'before' | 'after' {
  const rect = el.getBoundingClientRect();
  return clientY < rect.top + rect.height / 2 ? 'before' : 'after';
}

export function SceneBlock({
  scene,
  index,
  blockIndexBase = 0,
  onFocusElement,
  typeCommand,
  format = 'hollywood',
  mentionCandidates = [],
  castColors,
  locationCandidates = [],
  onSyncStateChange,
  reorder,
  onExitEditing,
  copilotActiveSceneId,
  onCopilotActivate,
  onElementsChange,
  focusPresence,
  selfActorId,
  onRegisterRemoteApply,
  remoteStale,
  onRemoteStaleHandled,
  elementDrag,
  onElementDragBegin,
  onElementDragDone,
  onCrossSceneDelete,
  onRegisterExternalOps,
  pageSeams,
}: SceneBlockProps) {
  const { t } = useTranslation();
  // SceneBlock also mounts in provider-less hosts (isolated embeds, bare unit
  // mounts) where a toast is a nice-to-have, not a dependency — hence the
  // optional variant instead of the throwing useToast.
  const toast = useOptionalToast();
  // Scoping for a scene-spawned issue. Present on the fullscreen editor route
  // (team/:teamId/projects/:projectId/scripts/:scriptId); absent in isolated
  // embeds, where the issue is simply created unscoped.
  const { teamId, projectId } = useParams();
  const sync = useSceneSync(scene, { selfActorId });
  const [mention, setMention] = useState<MentionState | null>(null);
  // Mention nav state owned HERE (the combobox is presentational): the active
  // option index + the shared listbox id feed both the popup and the ARIA
  // combobox attributes that ride on the focused line.
  const mentionListId = useId();
  const [mentionActive, setMentionActive] = useState(0);
  // Copilot (Task 11): elements selected via their gutter ticks + this turn's
  // applied-edit count and the inverse batch that undoes them.
  const [copilotSelection, setCopilotSelection] = useState<string[]>([]);
  const [copilotAnchor, setCopilotAnchor] = useState<string | null>(null);
  const [copilotEdits, setCopilotEdits] = useState<number | null>(null);
  const [copilotInverse, setCopilotInverse] = useState<ElementOp[] | null>(null);
  // Copilot-OWNED lifecycle: 'applying' only between our own dispatch and the
  // FIRST terminal saveState after it. Deriving the phase from the shared
  // scene saveState alone misreports unrelated saves (a normal edit after a
  // polish would flip the card back to "Polishing…") — review finding F4-2.
  const [copilotStatus, setCopilotStatus] = useState<'applying' | 'done' | 'failed' | null>(null);
  useEffect(() => {
    if (copilotStatus !== 'applying') return;
    if (sync.saveState === 'saved') setCopilotStatus('done');
    else if (sync.saveState === 'retrying' || sync.saveState === 'conflict') {
      setCopilotStatus('failed');
    }
  }, [copilotStatus, sync.saveState]);
  // Free-text reconciler (Task 8): the instruction box, the in-flight LLM
  // round-trip ('thinking' precedes any dispatch, so it is tracked separately
  // from the dispatch-driven copilotStatus), a pending proposal awaiting review,
  // the done-state summary, a 422 detail, and the 404 (flag-off) memory that
  // disables the box for the rest of this session.
  const [copilotInstruction, setCopilotInstruction] = useState('');
  const [copilotRequest, setCopilotRequest] = useState<'idle' | 'thinking'>('idle');
  const [copilotProposal, setCopilotProposal] = useState<{
    ops: ElementOp[];
    summary: string;
  } | null>(null);
  const [copilotSummary, setCopilotSummary] = useState<string | null>(null);
  const [copilotFailedDetail, setCopilotFailedDetail] = useState<string | null>(null);
  const [copilotDisabled, setCopilotDisabled] = useState(false);
  const [meta, setMeta] = useState<SceneMeta>({
    heading_int_ext: scene.heading_int_ext ?? '',
    location_text: scene.location_text ?? '',
    time_of_day: scene.time_of_day ?? '',
  });
  // Head row (laper parity): the three heading tokens (INT/EXT · location · time)
  // are ALWAYS rendered inline — there is no read/edit mode swap. Clicking a token
  // opens only that token's dropdown, so the slug never re-lays-out on click.
  // Element-level drag-to-reorder (hover-gutter 6-dot handle): the element being
  // dragged + the live drop target (which row + edge). Kept within this scene —
  // v1 does not support cross-scene element moves.
  const [draggingElementId, setDraggingElementId] = useState<string | null>(null);
  const [elementDropTarget, setElementDropTarget] = useState<{
    elementId: string;
    edge: 'top' | 'bottom';
  } | null>(null);
  // Right-click menu (heading or an element row): viewport point + which row was
  // clicked (`elementId: null` = the scene heading itself).
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    elementId: string | null;
  } | null>(null);
  // "Move whole scene" mode (from the context menu): while armed, an overlay over
  // the block is draggable and starts a WHOLE-SCENE drag (vs. a single-block drag
  // from the element grip). Cleared on drop / dragend / Esc / outside click.
  const [sceneMoveArmed, setSceneMoveArmed] = useState(false);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const headRowRef = useRef<HTMLDivElement | null>(null);
  // Tab flow across the head-row pickers (INT/EXT → location → time): focus the
  // Nth `.mh-scene-select` trigger (all three HeadingSelects render one).
  const focusHeadField = useCallback((idx: number) => {
    const triggers = headRowRef.current?.querySelectorAll<HTMLElement>('.mh-scene-select');
    triggers?.[idx]?.focus();
  }, []);
  const tiptapRef = useRef<TipTapSceneEditorHandle>(null);
  const elementsRef = useRef<ScriptElement[]>(sync.elements);
  const inputTimersRef = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  // Text buffered behind each input debounce — flushed (not dropped) on unmount
  // so a view switch mid-keystroke never loses writing (spec §3.6 flush guard).
  const pendingInputRef = useRef<Record<string, string>>({});
  const pendingMetaRef = useRef<Partial<SceneMeta> | null>(null);
  // Latest sync/scene-id for the []-dep unmount flush (avoids stale closures).
  const syncRef = useRef(sync);
  syncRef.current = sync;
  const sceneIdRef = useRef(scene.id);
  sceneIdRef.current = scene.id;
  const onSyncStateChangeRef = useRef(onSyncStateChange);
  onSyncStateChangeRef.current = onSyncStateChange;
  const metaTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mentionRef = useRef<MentionState | null>(null);

  // Filtered candidates for the open picker; kept in a ref so the keydown
  // handler (a stable callback) reads the latest list without re-subscribing.
  // The transition preset picker rides the same pipeline with its own list.
  const activeMentionCandidates = useMemo(() => {
    if (mention?.kind === 'transition') return TRANSITION_PRESETS;
    // Character cue: hoist the clicked cue's OWN name to the top so it leads the
    // (empty-query) list and becomes the initial highlight — laper parity — with
    // every other cast member keeping its existing order. A no-op when the cue is
    // empty (nothing to hoist) or its name isn't in the cast yet (fall through to
    // the plain list). Both the popup and the keyboard-nav entries read this list,
    // so they stay in lockstep.
    if (mention?.kind === 'character') {
      const current = sync.elements.find((e) => e.id === mention.elementId)?.text.trim();
      if (current) {
        const match = mentionCandidates.find((c) => c.toUpperCase() === current.toUpperCase());
        if (match) return [match, ...mentionCandidates.filter((c) => c !== match)];
      }
    }
    return mentionCandidates;
  }, [mention, mentionCandidates, sync.elements]);
  // The picker's navigable rows — filtered cast PLUS a synthetic "create" row
  // for a character cue whose typed name matches nothing exactly (location-field
  // parity). Enter on that row writes the new name, which IS how a character is
  // born (the cast is the set of distinct cues). Shared verbatim with the popup.
  const { entries: mentionEntriesList } = useMemo(
    () => (mention ? mentionEntries(activeMentionCandidates, mention.query, mention.kind) : { entries: [], createIndex: -1 }),
    [mention, activeMentionCandidates],
  );
  const mentionEntriesRef = useRef<string[]>(mentionEntriesList);
  const mentionActiveRef = useRef(0);

  // ── Slash menu (`/` at block start → block-type picker) ──────────────────
  // Mirrors the mention picker's state/ref shape exactly: the open state +
  // filtered items live in state, and refs feed the stable keydown handler.
  const [slash, setSlash] = useState<{
    elementId: string;
    query: string;
    position?: { top: number; left: number };
  } | null>(null);
  const [slashActive, setSlashActive] = useState(0);
  const slashListId = useId();
  const slashRef = useRef<typeof slash>(null);
  const slashFiltered = useMemo(
    () => (slash ? filterSlashItems(SLASH_ITEMS, slash.query, t) : []),
    [slash, t],
  );
  const slashFilteredRef = useRef<SlashItem[]>(slashFiltered);
  const slashActiveRef = useRef(0);

  elementsRef.current = sync.elements;
  mentionRef.current = mention;
  mentionEntriesRef.current = mentionEntriesList;
  mentionActiveRef.current = mentionActive;
  slashRef.current = slash;
  slashFilteredRef.current = slashFiltered;
  slashActiveRef.current = slashActive;

  // Reset the active option to the top whenever the picker opens or its filter
  // changes (typing narrows the list); nav-only changes must NOT reset it.
  useEffect(() => {
    setMentionActive(0);
  }, [mention?.elementId, mention?.query, mentionCandidates]);

  // Dismiss the slash / @-mention / cue pickers on an OUTSIDE click. The legacy
  // engine closed them when the contentEditable blurred; TipTap only blurs the
  // editor (which doesn't touch this scene's picker state), so a click on empty
  // paper or elsewhere used to leave the popup floating. Both the editor and the
  // popups live inside `containerRef`, so any mousedown whose target is NOT in
  // there is an outside click. (Clicking a popup option lands inside the
  // container, so its own onMouseDown→select still runs and wins.)
  useEffect(() => {
    if (!mention && !slash) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setMention(null);
        setSlash(null);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [mention, slash]);

  useEffect(() => {
    const inputTimers = inputTimersRef.current;
    return () => {
      // FLUSH, don't drop: a pending debounce at unmount (view switch, nav)
      // carries real keystrokes. dispatchOps still enqueues + fires the network
      // write after unmount (useSceneSync guards its setState via mountedRef).
      Object.values(inputTimers).forEach(clearTimeout);
      const pending = pendingInputRef.current;
      const ids = Object.keys(pending);
      if (ids.length > 0) {
        const ops: ElementOp[] = ids.map((elementId) => ({
          op: 'update',
          element_id: elementId,
          payload: { text: pending[elementId] },
        }));
        syncRef.current.dispatchOps(ops, applyLocal(elementsRef.current, ops));
        pendingInputRef.current = {};
      }
      onSyncStateChangeRef.current?.(sceneIdRef.current, {
        saveState: 'saved',
        resolveConflict: () => undefined,
      });
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
      const metaPatch = pendingMetaRef.current;
      if (metaPatch) {
        pendingMetaRef.current = null;
        updateSceneMeta(sceneIdRef.current, {
          heading_int_ext: metaPatch.heading_int_ext ?? undefined,
          location_text: metaPatch.location_text ?? undefined,
          time_of_day: metaPatch.time_of_day ?? undefined,
        }).catch((err) => console.error('[SceneBlock] flush updateSceneMeta failed', err));
      }
    };
  }, []);

  // Toolbar retype: when a command targets this scene, update the focused
  // element's type in place (only if it still exists in the optimistic view).
  const lastCommandNonceRef = useRef(0);
  useEffect(() => {
    if (!typeCommand || typeCommand.sceneId !== scene.id) return;
    if (typeCommand.nonce === lastCommandNonceRef.current) return;
    lastCommandNonceRef.current = typeCommand.nonce;
    const target = elementsRef.current.find((el) => el.id === typeCommand.elementId);
    if (!target) return;
    const op: ElementOp = {
      op: 'update',
      element_id: typeCommand.elementId,
      payload: { type: typeCommand.type },
    };
    // Apply the attrs-only transaction immediately (caret-preserving) via the
    // ref method; the ops dispatch below still lands (data plane unchanged) and
    // the applyExternalElements effect finds the doc already matches, so it
    // no-ops rather than rebuilding.
    tiptapRef.current?.retypeElement(typeCommand.elementId, typeCommand.type);
    sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
    // The toolbar button stole focus on click — hand it straight back to the
    // retyped line so the writer keeps typing (and the type-driven pickers,
    // e.g. the transition presets, open on the resulting selection update).
    tiptapRef.current?.focusElement(typeCommand.elementId);
  }, [typeCommand, scene.id, sync]);

  // Lift this scene's save state to the shell whenever it changes.
  useEffect(() => {
    onSyncStateChange?.(scene.id, {
      saveState: sync.saveState,
      resolveConflict: sync.resolveConflict,
    });
  }, [scene.id, sync.saveState, sync.resolveConflict, onSyncStateChange]);

  // Register this scene's applyRemoteOps with the shell's op-stream router so
  // realtime rows for this scene reach it; deregister on unmount / scene swap (C2).
  // scene.id is typed string but the scenes API returns it as a JSON number
  // (#1006), and the router's lookup key is String(record.scene_id) from the
  // realtime row — so we String() the registration key to match (mixed-type Map
  // keys silently miss).
  useEffect(() => {
    if (!onRegisterRemoteApply) return;
    const key = String(scene.id);
    onRegisterRemoteApply(key, sync.applyRemoteOps);
    return () => onRegisterRemoteApply(key, null);
  }, [scene.id, sync.applyRemoteOps, onRegisterRemoteApply]);

  // A remote op landed for this scene while it was windowed out — the snapshot
  // we mounted from may be stale, so refetch once and clear the flag (C2 / M1).
  useEffect(() => {
    if (!remoteStale) return;
    sync.reconcile();
    onRemoteStaleHandled?.(String(scene.id)); // string key — matches droppedScenes
  }, [remoteStale, scene.id, sync.reconcile, onRemoteStaleHandled]);

  // Route every `sync.elements` change (remote splice, reconcile, conflict
  // resolution, 409 replay) through the imperative applyExternalElements API.
  // This ALSO fires after our own local edits (dispatchOps → useSceneSync's
  // setElements), but that's harmless — applyExternalElements' own field-wise
  // equality check makes those calls a no-op (the doc already shows exactly
  // what `sync.elements` now says, since the edit originated FROM the editor),
  // which is the loop guard.
  useEffect(() => {
    tiptapRef.current?.applyExternalElements(sync.elements);
  }, [sync.elements]);

  // EmptySceneHint's seed action in TipTap mode: the PM schema requires
  // `scriptElement+` (at least one node), so an empty scene can never mount
  // a TipTapSceneEditor — seed the first element via the same anchored
  // insert op the legacy path's onEnter(…, {elementId: null}) issues, and
  // the effect above hands the freshly non-empty `sync.elements` to a
  // freshly-mounted editor.
  const handleTiptapSeed = useCallback(() => {
    const id = newElementId();
    const op: ElementOp = { op: 'insert', element_id: id, payload: { type: 'action', text: '' } };
    sync.dispatchOps([op], applyLocal(sync.elements, [op]));
    // M2 carry-over from M1: focus the freshly-seeded element. The editor
    // doesn't exist yet on THIS render (EmptySceneHint→TipTapSceneEditor is
    // a fresh mount triggered by `sync.elements` going non-empty) — rAF
    // runs after React has committed the new mount (same pattern as the
    // legacy machine's `applyResult` focus-the-new-cursor call below).
    requestAnimationFrame(() => tiptapRef.current?.focusElement(id, true));
  }, [sync]);

  // Selection changes report the focused element up to the shell (toolbar
  // follow / Statistics cursor).
  const handleTiptapFocusCursor = useCallback(
    (elementId: string | null) => {
      onFocusElement?.({ sceneId: scene.id, elementId, field: 'element' });
    },
    [onFocusElement, scene.id],
  );

  // ── TipTap M2: slash menu ('/' at block start) ────────────────────────
  // Mirrors legacy `handleInput`'s slash branch: a fresh '/' opens/updates
  // the picker (position looked up via the SAME `data-el-id` DOM query
  // legacy uses — the NodeViewContent div carries the identical attribute);
  // anything else closes it, but only if IT was the one open.
  const handleTiptapSlashChange = useCallback((elementId: string, query: string | null) => {
    if (query !== null) {
      const container = containerRef.current;
      const node = container?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
      // Measure relative to the scene block (the popup's positioning context) —
      // see handleTiptapMentionOpen for why offsetTop (row-relative) is wrong.
      let position: { top: number; left: number } | undefined;
      if (node && container) {
        const nodeRect = node.getBoundingClientRect();
        const contRect = container.getBoundingClientRect();
        position = { top: nodeRect.bottom - contRect.top, left: nodeRect.left - contRect.left };
      }
      setSlashActive(0);
      setSlash({ elementId, query, position });
    } else if (slashRef.current?.elementId === elementId) {
      setSlash(null);
    }
  }, []);

  // Apply a slash pick in TipTap mode: an attrs+text-clear transaction for
  // the immediate visual update (see TipTapSceneEditor's module doc) plus
  // the SAME update op legacy dispatches (data plane unchanged).
  const applySlashTiptap = useCallback(
    (type: ElementType) => {
      const s = slashRef.current;
      if (!s) return;
      const op: ElementOp = { op: 'update', element_id: s.elementId, payload: { type, text: '' } };
      tiptapRef.current?.retypeElement(s.elementId, type, true);
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
      setSlash(null);
    },
    [sync],
  );

  const tiptapSlashMenu: MenuBridge | null = useMemo(() => {
    if (!slash) return null;
    return {
      elementId: slash.elementId,
      onArrowDown: () =>
        setSlashActive((prev) => (slashFilteredRef.current.length > 0 ? (prev + 1) % slashFilteredRef.current.length : prev)),
      onArrowUp: () =>
        setSlashActive((prev) =>
          slashFilteredRef.current.length > 0
            ? (prev - 1 + slashFilteredRef.current.length) % slashFilteredRef.current.length
            : prev,
        ),
      onApply: () => {
        const filtered = slashFilteredRef.current;
        const active = slashActiveRef.current;
        if (filtered.length > 0 && active >= 0 && active < filtered.length) {
          applySlashTiptap(filtered[active].type);
        } else {
          setSlash(null);
        }
      },
      onEscape: () => setSlash(null),
    };
  }, [slash, applySlashTiptap]);

  // ── TipTap M2: mentions + character-cue picker ────────────────────────
  const handleTiptapMentionOpen = useCallback(
    (elementId: string, kind: 'inline' | 'character' | 'transition', query: string) => {
      const container = containerRef.current;
      const node = container?.querySelector<HTMLElement>(`[data-el-id="${elementId}"]`);
      let position: { top: number; left: number; centered?: boolean } | undefined;
      if (node && container) {
        // The popup is absolutely positioned relative to `.mh-scene-block`
        // (containerRef). The line's own `offsetTop` is relative to its
        // `.mh-el-row` (which is position:relative), NOT the scene block — so
        // using it placed the picker near the TOP of the block, ON TOP of the
        // cue being typed. Measure the line's box relative to the container so
        // the picker always sits just BELOW it, wherever the row is.
        const nodeRect = node.getBoundingClientRect();
        const contRect = container.getBoundingClientRect();
        const top = nodeRect.bottom - contRect.top;
        // Panel width bound (CSS max-width) for edge clamping.
        const POPUP_WIDTH = 300;
        if (kind === 'character') {
          // A character cue's NAME no longer sits at the row's left edge — the
          // Hollywood cue is a centered pill and the Asian cue shrink-wraps
          // (顶格). laper parity (user 2026-07-20): the picker hangs CENTERED
          // directly under the cue. Hand the combobox the cue box's CENTER
          // (`centered` → translateX(-50%), exact at any rendered width): the
          // NodeViewContent's inner wrapper (`.hw-character > *` / the asian
          // equivalent) carries the pill/name; an EMPTY cue's wrapper spans the
          // full row, whose centre is exactly where the centered "Character"
          // whisper (and caret) sit. Clamp the centre so the panel's worst-case
          // half-width never spills either sheet edge.
          const inner = node.firstElementChild as HTMLElement | null;
          const anchorRect = (inner ?? node).getBoundingClientRect();
          let center = anchorRect.left - contRect.left + anchorRect.width / 2;
          center = Math.max(
            POPUP_WIDTH / 2,
            Math.min(center, container.clientWidth - POPUP_WIDTH / 2),
          );
          position = { top, left: center, centered: true };
        } else {
          // Clamp so the panel never spills past the sheet's right edge.
          let left = nodeRect.left - contRect.left;
          const maxLeft = container.clientWidth - POPUP_WIDTH;
          if (left > maxLeft) left = Math.max(0, maxLeft);
          position = { top, left };
        }
      }
      setMention({ elementId, kind, query, position });
    },
    [],
  );
  const handleTiptapMentionClose = useCallback(() => setMention(null), []);

  // Typing/keyboard path for a character line: refresh the query of a picker
  // that is ALREADY open on this element, and do nothing otherwise. Never opens
  // one — the editor decides that (a click on the name, or an empty cue). The
  // `prev` bail-outs also mean this can't resurrect a picker the writer just
  // dismissed with Escape while the caret stayed in the line.
  const handleTiptapMentionQuery = useCallback((elementId: string, query: string) => {
    setMention((prev) =>
      prev && prev.elementId === elementId && prev.query !== query ? { ...prev, query } : prev,
    );
  }, []);

  const handleTiptapMentionSelect = useCallback(
    (name: string) => {
      const m = mentionRef.current;
      if (!m) return;
      let newText: string;
      if (m.kind !== 'inline') {
        // A character cue IS the name; a transition IS the preset — either
        // way the picked option replaces the whole line.
        newText = name;
      } else {
        const el = elementsRef.current.find((e) => e.id === m.elementId);
        const text = el?.text ?? '';
        const at = text.lastIndexOf('@');
        newText =
          at >= 0
            ? `${text.slice(0, at)}@${name} ${text.slice(at + 1 + m.query.length)}`
            : `${text}@${name} `;
      }
      const ops: ElementOp[] = [
        { op: 'update', element_id: m.elementId, payload: { text: newText } },
      ];
      tiptapRef.current?.replaceElementText(m.elementId, newText);
      // Selecting from the popup's embedded search input leaves focus there —
      // focusElement reclaims the editor. Its synchronous selection-update may
      // re-open the cue picker, but the setMention(null) below runs after and
      // wins.
      if (m.kind === 'character') {
        // laper: picking a cue flows straight into writing the line — land the
        // caret in the dialogue below, creating it when the cue has none yet.
        const dialogue = tiptapRef.current?.ensureDialogueAfter(m.elementId, newElementId());
        if (dialogue?.inserted) {
          ops.push({
            op: 'insert',
            element_id: dialogue.id,
            after_id: m.elementId,
            payload: { type: 'dialogue', text: '' },
          });
        }
        tiptapRef.current?.focusElement(dialogue ? dialogue.id : m.elementId, true);
      } else {
        tiptapRef.current?.focusElement(m.elementId);
      }
      sync.dispatchOps(ops, applyLocal(elementsRef.current, ops));
      setMention(null);
    },
    [sync],
  );

  // ── Popup-embedded search input paths (laper cue picker) ───────────────
  // The character-cue popup carries its own input; these mirror the line's
  // keyboard semantics for keystrokes that happen INSIDE that input.
  const refocusMentionLine = useCallback((elementId: string) => {
    tiptapRef.current?.focusElement(elementId);
  }, []);

  const handleMentionQueryChange = useCallback((q: string) => {
    setMention((prev) => (prev ? { ...prev, query: q } : prev));
  }, []);

  // Tab in the popup input: abandon the cue, revert the block to action
  // (same semantics as Tab on the line — spec §3.2 laper behaviour).
  const handleMentionTabAction = useCallback(() => {
    const m = mentionRef.current;
    if (!m) return;
    if (m.kind === 'character') {
      const op: ElementOp = { op: 'update', element_id: m.elementId, payload: { type: 'action' } };
      tiptapRef.current?.retypeElement(m.elementId, 'action');
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
    }
    refocusMentionLine(m.elementId);
    setMention(null);
  }, [sync, refocusMentionLine]);

  // Escape in the popup input: close and hand focus back to the line. Refocus
  // FIRST — its selection-update may re-open the picker, and the close below
  // must win.
  const handleMentionPopClose = useCallback(() => {
    const m = mentionRef.current;
    if (m) refocusMentionLine(m.elementId);
    setMention(null);
  }, [refocusMentionLine]);

  const tiptapMentionMenu: MenuBridge | null = useMemo(() => {
    if (!mention) return null;
    return {
      elementId: mention.elementId,
      onArrowDown: () =>
        setMentionActive((prev) =>
          mentionEntriesRef.current.length > 0 ? (prev + 1) % mentionEntriesRef.current.length : prev,
        ),
      onArrowUp: () =>
        setMentionActive((prev) =>
          mentionEntriesRef.current.length > 0
            ? (prev - 1 + mentionEntriesRef.current.length) % mentionEntriesRef.current.length
            : prev,
        ),
      onApply: () => {
        // entries carries the create row too (its value IS the typed name), so
        // committing entries[active] both selects a cast member AND coins a new
        // one — the line-driven Enter no longer just closes on a miss.
        const entries = mentionEntriesRef.current;
        const active = mentionActiveRef.current;
        if (entries.length > 0 && active >= 0 && active < entries.length) {
          handleTiptapMentionSelect(entries[active]);
        } else {
          setMention(null);
        }
      },
      onTab: () => {
        // Transition preset picker: decline the key so Tab falls through to
        // the ScriptKeymap's real type-cycle (see MenuBridge.onTab contract).
        if (mention.kind === 'transition') return false;
        if (mention.kind === 'character') {
          const op: ElementOp = { op: 'update', element_id: mention.elementId, payload: { type: 'action' } };
          tiptapRef.current?.retypeElement(mention.elementId, 'action');
          sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
        }
        setMention(null);
      },
      onEscape: () => setMention(null),
    };
  }, [mention, handleTiptapMentionSelect, sync]);

  // Lift optimistic elements to the shell for live Statistics + rail entities
  // (Task 6 ⑥). STRUCTURAL changes (a block created/deleted/moved/retyped —
  // via Tab, slash menu, toolbar, Enter) report IMMEDIATELY so anything
  // derived from types (the toolbar's active pill) never lags; pure text
  // typing keeps the 1s debounce so a keystroke burst collapses into one
  // update.
  const lastSkeletonRef = useRef('');
  useEffect(() => {
    if (!onElementsChange) return;
    const skeleton = sync.elements.map((e) => `${e.id}:${e.type}`).join('|');
    if (skeleton !== lastSkeletonRef.current) {
      lastSkeletonRef.current = skeleton;
      onElementsChange(scene.id, sync.elements);
      return;
    }
    const id = setTimeout(() => onElementsChange(scene.id, sync.elements), 1000);
    return () => clearTimeout(id);
  }, [sync.elements, scene.id, onElementsChange]);


  const commitMeta = useCallback(
    (patch: Partial<SceneMeta>) => {
      setMeta((prev) => ({ ...prev, ...patch }));
      pendingMetaRef.current = { ...(pendingMetaRef.current ?? {}), ...patch };
      if (metaTimerRef.current) clearTimeout(metaTimerRef.current);
      metaTimerRef.current = setTimeout(() => {
        pendingMetaRef.current = null;
        updateSceneMeta(scene.id, {
          heading_int_ext: patch.heading_int_ext ?? undefined,
          location_text: patch.location_text ?? undefined,
          time_of_day: patch.time_of_day ?? undefined,
        }).catch((err) => console.error('[SceneBlock] updateSceneMeta failed', err));
      }, META_DEBOUNCE_MS);
    },
    [scene.id],
  );

  // ── Reorder wiring (Task 10) ──────────────────────────────────────────────
  const isDragging = reorder?.draggingId === scene.id;
  const dropEdge =
    reorder?.dropTarget && reorder.dropTarget.sceneId === scene.id
      ? reorder.dropTarget.edge
      : null;

  const handleHandleKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (!reorder || !e.altKey) return;
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      reorder.onKeyboardMove(scene.id, 'up');
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      reorder.onKeyboardMove(scene.id, 'down');
    }
  };

  const handleBlockDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!reorder || !reorder.draggingId || reorder.draggingId === scene.id) return;
    // preventDefault marks this a valid drop target (HTML5 DnD contract).
    e.preventDefault();
    reorder.onDragOver(scene.id, edgeFromPointer(e.currentTarget, e.clientY));
  };
  const handleBlockDrop = (e: DragEvent<HTMLDivElement>) => {
    if (!reorder || !reorder.draggingId) return;
    e.preventDefault();
    reorder.onDrop(scene.id, edgeFromPointer(e.currentTarget, e.clientY));
  };

  // ── Element reorder wiring (hover-gutter handle) ──────────────────────────
  // Mirrors the scene-level pattern above but drives a `move` op WITHIN this
  // scene. Edge 'top' lands the dragged element BEFORE the target (before_id),
  // 'bottom' lands it AFTER (after_id); applyLocal re-anchors, and useSceneSync
  // dispatches the op optimistically (route-C: no direct phase writes).
  //
  // CROSS-SCENE: a drag whose source is ANOTHER scene (shell-broadcast via the
  // `elementDrag` prop) is accepted too — the drop inserts the element here
  // (insert op, full payload, same id) and asks the shell to delete it from
  // the source scene. Insert-then-delete order so a failure can only duplicate,
  // never lose, the paragraph.
  const externalDrag = elementDrag && elementDrag.sceneId !== scene.id ? elementDrag : null;

  // Register a dispatcher the shell can use for the cross-scene delete leg.
  useEffect(() => {
    if (!onRegisterExternalOps) return;
    onRegisterExternalOps(scene.id, (ops) => {
      syncRef.current.dispatchOps(ops, applyLocal(elementsRef.current, ops));
    });
    return () => onRegisterExternalOps(scene.id, null);
  }, [scene.id, onRegisterExternalOps]);

  const handleElementDragStart = useCallback(
    (elementId: string) => {
      setDraggingElementId(elementId);
      const el = elementsRef.current.find((e) => e.id === elementId);
      if (el) onElementDragBegin?.(scene.id, el);
    },
    [scene.id, onElementDragBegin],
  );
  const handleElementDragEnd = useCallback(() => {
    setDraggingElementId(null);
    setElementDropTarget(null);
    onElementDragDone?.();
  }, [onElementDragDone]);
  const handleElementDragOver = useCallback((elementId: string, edge: 'top' | 'bottom') => {
    // Bail out when the target row+edge is UNCHANGED. `dragover` fires ~60×/s and
    // a fresh object literal every time made this state always-different, so each
    // one re-rendered the block → propsSync no-op transaction → ProseMirror
    // repainted every row. That is what made dragging stutter. Returning `prev`
    // lets React skip the render entirely, so we only repaint when the drop
    // indicator actually moves. Mirrors the scene-level guard EditorShell's
    // `reorder.onDragOver` has always had.
    setElementDropTarget((prev) =>
      prev && prev.elementId === elementId && prev.edge === edge ? prev : { elementId, edge },
    );
  }, []);
  /** Build the insert op that lands an external element at the given anchor. */
  const acceptExternalDrop = useCallback(
    (anchor: { before_id?: string; after_id?: string }) => {
      if (!externalDrag) return;
      const { sceneId: fromSceneId, element } = externalDrag;
      const op: ElementOp = {
        op: 'insert',
        element_id: element.id,
        payload: {
          type: element.type,
          text: element.text,
          ...(element.character_id ? { character_id: element.character_id } : {}),
        },
        ...anchor,
      };
      sync.dispatchOps([op], applyLocal(sync.elements, [op]));
      onCrossSceneDelete?.(fromSceneId, element.id);
      onElementDragDone?.();
      // Focus the newly-landed element once the applyExternalElements effect
      // (triggered by the `sync.elements` change above) has rebuilt the doc —
      // rAF runs after React commits.
      const droppedId = element.id;
      requestAnimationFrame(() => tiptapRef.current?.focusElement(droppedId));
    },
    [externalDrag, sync, onCrossSceneDelete, onElementDragDone],
  );
  const handleElementDrop = useCallback(
    (targetId: string, edge: 'top' | 'bottom') => {
      setElementDropTarget(null);
      const dragging = draggingElementId;
      setDraggingElementId(null);
      if (dragging) {
        // Same-scene reorder (existing move-op path). Dropping onto itself is a
        // no-op; applyMove would also self-anchor-skip, but bailing here avoids
        // an empty dispatch + version bump.
        if (dragging === targetId) return;
        const op: ElementOp =
          edge === 'top'
            ? { op: 'move', element_id: dragging, before_id: targetId }
            : { op: 'move', element_id: dragging, after_id: targetId };
        sync.dispatchOps([op], applyLocal(sync.elements, [op]));
        return;
      }
      if (externalDrag) {
        acceptExternalDrop(edge === 'top' ? { before_id: targetId } : { after_id: targetId });
      }
    },
    [draggingElementId, sync, externalDrag, acceptExternalDrop],
  );
  // External drop on the scene HEADING row → insert at the head of this scene
  // (covers empty scenes, which have no element rows to target).
  const handleHeadRowExternalDrop = useCallback(() => {
    if (!externalDrag) return;
    const firstId = elementsRef.current[0]?.id;
    acceptExternalDrop(firstId ? { before_id: firstId } : {});
  }, [externalDrag, acceptExternalDrop]);

  // ── Right-click context menu (delete block / delete scene / move scene) ─────
  const closeContextMenu = useCallback(() => setContextMenu(null), []);
  // Heading right-click: elementId is null (the row IS the scene heading).
  const openHeadingContextMenu = useCallback((e: ReactMouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY, elementId: null });
  }, []);
  // Element-row right-click, forwarded up from the TipTap NodeView.
  const handleElementContextMenu = useCallback((elementId: string, x: number, y: number) => {
    setContextMenu({ x, y, elementId });
  }, []);
  // Delete a single element (the right-clicked block). Idempotent delete op; if it
  // was the scene's last element the scene collapses to the EmptySceneHint.
  const deleteElement = useCallback(
    (elementId: string) => {
      const op: ElementOp = { op: 'delete', element_id: elementId };
      sync.dispatchOps([op], applyLocal(elementsRef.current, [op]));
    },
    [sync],
  );
  // Arm whole-scene move mode — the drag overlay (rendered below) takes over.
  const armSceneMove = useCallback(() => {
    if (reorder) setSceneMoveArmed(true);
  }, [reorder]);
  const disarmSceneMove = useCallback(() => setSceneMoveArmed(false), []);

  /**
   * Turn this scene into a to-do — the script-editor twin of the canvas card's
   * 'Create issue' (PR #1402). origin_kind stays 'manual' (a person clicked it);
   * the scene is recorded in origin_id as `scene:{id}`, which the issue's Related
   * tab reads to link back. The id is a Snowflake BIGINT and is passed straight
   * through as a string — never Number()-coerced (precision loss past 2^53).
   * team/project scope come from the editor route so the issue lands where the
   * script lives.
   */
  const createIssueFromScene = useCallback(async () => {
    const slug = [scene.heading_int_ext, scene.location_text, scene.time_of_day]
      .map((part) => (part ?? '').trim())
      .filter(Boolean)
      .join(' · ');
    const label = slug ? `Scene ${index + 1} — ${slug}` : `Scene ${index + 1}`;
    try {
      const issue = await createIssue({
        title: `Follow up: ${label}`,
        origin_id: buildOriginId('scene', scene.id),
        ...(teamId ? { team_id: teamId } : {}),
        ...(projectId ? { project_id: projectId } : {}),
      });
      toast?.addToast(`Created ${issue.identifier} from this scene`, 'success');
    } catch (err) {
      console.error('[SceneBlock] create issue failed:', err);
      toast?.addToast(err instanceof Error ? err.message : 'Could not create issue', 'error');
    }
  }, [
    scene.id,
    scene.heading_int_ext,
    scene.location_text,
    scene.time_of_day,
    index,
    teamId,
    projectId,
    toast,
  ]);

  // The menu items are scoped to WHERE it opened. A paragraph (element) row
  // offers only paragraph-level actions (delete this block); the SCENE-level
  // actions (delete scene / move scene) live ONLY on the heading row. Mixing
  // "Move scene" into a paragraph's menu let a writer arm the whole-scene move
  // overlay while thinking they were acting on one paragraph, so a later drag on
  // the scene body silently moved the entire scene. Keeping the whole-scene
  // affordances on the heading row makes the target unambiguous. 'Create issue'
  // stays available in both (the scene is the issue's subject either way).
  // Reorder-dependent items hide when reorder is unwired (read-only / storyboard
  // embeds pass no reorder and keep a bare menu).
  const contextMenuItems = useMemo<SceneContextMenuItem[]>(() => {
    if (!contextMenu) return [];
    const items: SceneContextMenuItem[] = [];
    const elId = contextMenu.elementId;
    const onHeading = elId === null;
    if (elId) {
      items.push({
        key: 'delete-block',
        label: t('editor.ctxDeleteBlock'),
        danger: true,
        onSelect: () => deleteElement(elId),
      });
    }
    if (reorder) {
      items.push({
        key: 'create-issue',
        label: t('editor.ctxCreateIssue', 'Create issue'),
        dividerBefore: !!elId,
        onSelect: () => void createIssueFromScene(),
      });
      // Whole-scene actions belong to the heading row only — never a paragraph's
      // menu (see the whole-scene-move confusion above).
      if (onHeading) {
        items.push({
          key: 'delete-scene',
          label: t('editor.ctxDeleteScene'),
          danger: true,
          dividerBefore: true,
          onSelect: () => reorder.onDeleteScene(scene.id),
        });
        items.push({
          key: 'move-scene',
          label: t('editor.ctxMoveScene'),
          onSelect: armSceneMove,
        });
      }
    }
    return items;
  }, [contextMenu, reorder, scene.id, t, deleteElement, armSceneMove, createIssueFromScene]);

  // Esc / outside-click while move mode is armed cancels it (the overlay itself
  // disarms on dragend/drop). Armed only ever true when reorder is present.
  useEffect(() => {
    if (!sceneMoveArmed) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        disarmSceneMove();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [sceneMoveArmed, disarmSceneMove]);

  // ── Copilot summon (Task 11) ──────────────────────────────────────────────
  const clearCopilot = useCallback(() => {
    setCopilotSelection([]);
    setCopilotAnchor(null);
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotStatus(null);
    setCopilotInstruction('');
    setCopilotRequest('idle');
    setCopilotProposal(null);
    setCopilotSummary(null);
    setCopilotFailedDetail(null);
    // copilotDisabled is intentionally NOT reset: the 404 flag-off memory
    // persists for the session so we don't re-probe a known-off endpoint.
  }, []);

  const handleTickClick = useCallback(
    (elementId: string, shiftKey: boolean) => {
      // Any selection change starts a fresh turn (drop last turn's undo/edits).
      setCopilotEdits(null);
      setCopilotInverse(null);
      setCopilotSummary(null);
      setCopilotProposal(null);
      setCopilotFailedDetail(null);
      setCopilotStatus(null);
      const els = elementsRef.current;
      if (shiftKey && copilotAnchor) {
        const a = els.findIndex((e) => e.id === copilotAnchor);
        const b = els.findIndex((e) => e.id === elementId);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = a <= b ? [a, b] : [b, a];
          setCopilotSelection(els.slice(lo, hi + 1).map((e) => e.id));
          return;
        }
      }
      // Plain click: single-select, or toggle off if it was the only selection.
      setCopilotSelection((prev) => (prev.length === 1 && prev[0] === elementId ? [] : [elementId]));
      setCopilotAnchor(elementId);
    },
    [copilotAnchor],
  );

  const handlePolish = useCallback(() => {
    const els = elementsRef.current;
    const ops = buildPolishOps(els, copilotSelection);
    if (ops.length === 0) {
      setCopilotEdits(0);
      setCopilotInverse(null);
      setCopilotStatus('done');
      return;
    }
    const inverse = buildInverse(ops, els);
    sync.dispatchOps(ops, applyLocal(els, ops));
    setCopilotEdits(ops.length);
    setCopilotInverse(inverse);
    setCopilotStatus('applying');
  }, [copilotSelection, sync]);

  const handleCopilotUndo = useCallback(() => {
    const inverse = copilotInverse;
    if (!inverse || inverse.length === 0) return;
    sync.dispatchOps(inverse, applyLocal(elementsRef.current, inverse));
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotStatus(null);
    setCopilotSummary(null);
  }, [copilotInverse, sync]);

  // Apply reconciled ops the same way Polish does: store the inverse for Undo,
  // dispatch through the scene's op queue, and hand the phase to the shared
  // saveState-driven copilotStatus so it settles applying → done.
  const applyCopilotOps = useCallback(
    (ops: ElementOp[], summary: string) => {
      const els = elementsRef.current;
      const inverse = buildInverse(ops, els);
      // If-Match uses sync's CURRENT version; the ops were generated against
      // base_version. If a local edit advanced the version since the request
      // started, dispatch will 409 and flow through the existing conflict path
      // (acceptable — zero new concurrency surface, spec §2.2).
      sync.dispatchOps(ops, applyLocal(els, ops));
      setCopilotEdits(ops.length);
      setCopilotInverse(inverse);
      setCopilotSummary(summary);
      setCopilotProposal(null);
      setCopilotInstruction('');
      setCopilotRequest('idle');
      setCopilotStatus('applying');
    },
    [sync],
  );

  const handleCopilotSubmit = useCallback(() => {
    const instruction = copilotInstruction.trim();
    if (!instruction || copilotDisabled) return;
    // Fresh turn: drop any prior result/undo/proposal/error.
    setCopilotEdits(null);
    setCopilotInverse(null);
    setCopilotSummary(null);
    setCopilotProposal(null);
    setCopilotFailedDetail(null);
    setCopilotStatus(null);
    setCopilotRequest('thinking');
    const readVersion = sync.version;
    requestCopilotOps(scene.id, instruction, readVersion)
      .then((result) => {
        if (result.proposal) {
          // Stale read: do NOT auto-apply — park for review (Apply/Discard).
          setCopilotProposal({ ops: result.ops, summary: result.summary });
          setCopilotRequest('idle');
          return;
        }
        applyCopilotOps(result.ops, result.summary);
      })
      .catch((err) => {
        setCopilotRequest('idle');
        if (err instanceof CopilotDisabledError) {
          // Flag off (404): disable the box for the session — don't re-probe.
          setCopilotDisabled(true);
          return;
        }
        if (err instanceof OpRejectedError) {
          // 422: elements untouched; surface the op code, keep the instruction.
          setCopilotFailedDetail(String(err.code));
          setCopilotStatus('failed');
          return;
        }
        console.error('[SceneBlock] copilot request failed', err);
        setCopilotFailedDetail(null);
        setCopilotStatus('failed');
      });
  }, [copilotInstruction, copilotDisabled, scene.id, sync.version, applyCopilotOps]);

  const handleCopilotApply = useCallback(() => {
    const proposal = copilotProposal;
    if (!proposal) return;
    applyCopilotOps(proposal.ops, proposal.summary);
  }, [copilotProposal, applyCopilotOps]);

  const handleCopilotDiscard = useCallback(() => {
    setCopilotProposal(null);
    setCopilotRequest('idle');
  }, []);

  // Tell the shell which scene owns the card; if another scene takes over, drop
  // our selection so only one card is ever summoned (spec: non-persistent).
  const hasCopilotSelection = copilotSelection.length > 0;
  useEffect(() => {
    if (hasCopilotSelection) onCopilotActivate?.(scene.id);
  }, [hasCopilotSelection, scene.id, onCopilotActivate]);
  useEffect(() => {
    if (
      hasCopilotSelection &&
      copilotActiveSceneId != null &&
      copilotActiveSceneId !== scene.id
    ) {
      clearCopilot();
    }
  }, [copilotActiveSceneId, hasCopilotSelection, scene.id, clearCopilot]);

  const copilotSelectedIds = useMemo(() => new Set(copilotSelection), [copilotSelection]);

  const copilotPhase: CopilotPhase = (() => {
    if (copilotRequest === 'thinking') return 'applying';
    if (copilotProposal) return 'proposal';
    if (copilotStatus === 'failed') return 'failed';
    if (copilotEdits === null || copilotStatus === null) return 'attached';
    if (copilotStatus === 'applying') return 'applying';
    return 'done';
  })();

  return (
    <div
      className={`mh-scene-block${isDragging ? ' dragging' : ''}${
        sceneMoveArmed ? ' move-armed' : ''
      }`}
      ref={containerRef}
      data-testid="scene-block"
      data-scene-id={scene.id}
      onDragOver={reorder ? handleBlockDragOver : undefined}
      onDrop={reorder ? handleBlockDrop : undefined}
    >
      {dropEdge === 'before' && (
        <div className="mh-drop-indicator before" data-testid="drop-indicator" aria-hidden="true" />
      )}
      <div
        className={`mh-scene-headrow${format === 'asian' ? ' asian' : ''}`}
        ref={headRowRef}
        // Focus anywhere in the heading row (any of the three token selects —
        // focus bubbles) reports a HEADING cursor so the toolbar's active pill
        // switches to Scene.
        onFocus={() =>
          onFocusElement?.({ sceneId: scene.id, elementId: null, field: 'heading_int_ext' })
        }
        // Right-click anywhere on the heading row (including a token chip) → the
        // scene-level context menu (delete scene / move scene). preventDefault
        // suppresses the browser menu.
        onContextMenu={openHeadingContextMenu}
        // Cross-scene drop target: dropping a dragged paragraph on the heading
        // row lands it at the HEAD of this scene (works for empty scenes too).
        onDragOver={externalDrag ? (e) => e.preventDefault() : undefined}
        onDrop={
          externalDrag
            ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                handleHeadRowExternalDrop();
              }
            : undefined
        }
      >
        {/* IN-FLOW gutter (structural fix for the misalignment class): the
         *  number + drag handle live INSIDE the heading row's flex line, pulled
         *  into the left margin with a negative margin — they share the text's
         *  line box, so they can never drift vertically (the absolute+magic-top
         *  approach misaligned per font/row kind). Same pattern as .mh-el-gutter. */}
        <span className="mh-scene-gutter" contentEditable={false}>
          {/* A1: continuous document-order block number (scene heading = block
           *  `blockIndexBase + 1`), NOT the scene's position among scenes. */}
          <span className="mh-scene-num-badge">{blockIndexBase + 1}</span>
          <button
            type="button"
            className={`mh-drag-handle${isDragging ? ' dragging' : ''}`}
            aria-label={t('editor.dragScene')}
            // Disambiguates the two drag scopes: this handle (on the heading
            // row) moves the WHOLE scene; a paragraph's handle moves that
            // paragraph.
            title="Move scene"
            aria-grabbed={reorder ? isDragging : undefined}
            draggable={!!reorder}
            tabIndex={reorder ? 0 : -1}
            onDragStart={
              reorder
                ? (e) => {
                    e.dataTransfer.effectAllowed = 'move';
                    e.dataTransfer.setData('text/plain', scene.id);
                    e.dataTransfer.setData(MH_DRAG_MIME, scene.id);
                    reorder.onDragStart(scene.id);
                  }
                : undefined
            }
            onDragEnd={reorder ? () => reorder.onDragEnd() : undefined}
            onKeyDown={handleHandleKeyDown}
          >
            {/* 4-dot (2×2) grid — identical affordance to `.mh-el-drag`. */}
            {[0, 1, 2, 3].map((d) => (
              <span key={d} className="mh-el-dot" aria-hidden="true" />
            ))}
          </button>
        </span>
        {/* laper parity: the heading is ALWAYS three inline token dropdowns with
         *  STATIC separators between them — no read/edit mode swap. Clicking a
         *  token opens only that token's own dropdown; the slug never re-lays-out
         *  on click. A filled token reads as plain uppercase slug text, an unset
         *  one as a muted chip (see .mh-scene-select in editorShellStyles.ts) —
         *  so a partially-filled heading still reads INT. LOCATION - DAY. The
         *  separators (.·-) are decorative, so a fully-filled Hollywood heading
         *  still reads as the slug "INT. LOCATION - DAY". */}
        {/* Token order differs by format. Hollywood is the industry slug
         *  `INT. LOCATION - DAY`. Asian (华语) is `N. 地点 时间 / INT` — the scene
         *  NUMBER leads (inline bold, laper 亚洲格式), location + time sit as the
         *  centred content, and INT/EXT moves to the TAIL behind a ` / `. Tab flow
         *  follows visual order in both: onTabNext advances to the NEXT
         *  `.mh-scene-select` in DOM order (focusHeadField reads them positionally).
         *  The three HeadingSelects are otherwise identical — only their order,
         *  separators, and tab wiring change. */}
        {format === 'asian' ? (
          <span className="mh-scene-heading asian">
            {/* Asian 亚洲格式 scene numbers are sequential scene ordinals
             *  (1. 2. 3. …), matching the left rail's S1/S2 — NOT the continuous
             *  document-order block index (blockIndexBase) the Hollywood hover
             *  badge uses, which would jump 1 → 3 → 14 across scenes. */}
            <span className="mh-scene-num-inline" aria-hidden="true">
              {index + 1}.
            </span>
            <HeadingSelect
              searchable
              candidates={locationCandidates}
              value={meta.location_text}
              placeholder={t('editor.locationPlaceholder')}
              ariaLabel={t('editor.location')}
              tabHint={t('editor.headingTabTime')}
              onChange={(v) => commitMeta({ location_text: v })}
              onTabNext={() => focusHeadField(1)}
            />
            <span className="mh-heading-sep" aria-hidden="true">
              {' '}
            </span>
            <HeadingSelect
              value={meta.time_of_day}
              options={TIME_OPTIONS}
              placeholder="DAY/NIGHT"
              ariaLabel={t('editor.timeOfDay')}
              onChange={(v) => commitMeta({ time_of_day: v })}
              onTabNext={() => focusHeadField(2)}
            />
            <span className="mh-heading-sep" aria-hidden="true">
              {' / '}
            </span>
            <HeadingSelect
              value={meta.heading_int_ext}
              options={INT_EXT_OPTIONS}
              placeholder="INT/EXT"
              ariaLabel={t('editor.intExt')}
              onChange={(v) => commitMeta({ heading_int_ext: v })}
            />
          </span>
        ) : (
          <span className="mh-scene-heading">
            <HeadingSelect
              value={meta.heading_int_ext}
              options={INT_EXT_OPTIONS}
              placeholder="INT/EXT"
              ariaLabel={t('editor.intExt')}
              tabHint={t('editor.headingTabLocation')}
              onChange={(v) => commitMeta({ heading_int_ext: v })}
              onTabNext={() => focusHeadField(1)}
            />
            <span className="mh-heading-sep" aria-hidden="true">
              {'. '}
            </span>
            <HeadingSelect
              searchable
              candidates={locationCandidates}
              value={meta.location_text}
              placeholder={t('editor.locationPlaceholder')}
              ariaLabel={t('editor.location')}
              tabHint={t('editor.headingTabTime')}
              onChange={(v) => commitMeta({ location_text: v })}
              onTabNext={() => focusHeadField(2)}
            />
            <span className="mh-heading-sep" aria-hidden="true">
              {' - '}
            </span>
            <HeadingSelect
              value={meta.time_of_day}
              options={TIME_OPTIONS}
              placeholder="DAY/NIGHT"
              ariaLabel={t('editor.timeOfDay')}
              onChange={(v) => commitMeta({ time_of_day: v })}
            />
          </span>
        )}
        <ScenePresenceBadge users={focusPresence ?? []} />
      </div>

      {/* The PM schema requires at least one node, so an empty scene renders
          EmptySceneHint instead of mounting the editor, wired to an anchored
          insert op. */}
      {sync.elements.length === 0 ? (
        <EmptySceneHint onSeed={handleTiptapSeed} />
      ) : (
        <TipTapSceneEditor
          // Format is a mount-time snapshot (see TipTapSceneEditor's module
          // doc, "format switch recreates the editor") — keying on it forces a
          // full remount (flushing any pending debounce first) whenever the
          // script-wide Hollywood/Asian toggle flips, instead of trying to
          // live-patch the NodeView's structural DOM change.
          key={format}
          ref={tiptapRef}
          initialElements={sync.elements}
          format={format}
          blockIndexBase={blockIndexBase}
          dispatchOps={sync.dispatchOps}
          onFocusCursor={handleTiptapFocusCursor}
          onExitEditing={onExitEditing}
          onTickClick={handleTickClick}
          selectedElementIds={copilotSelectedIds}
          draggingElementId={draggingElementId ?? externalDrag?.element.id ?? null}
          dropElementEdge={elementDropTarget}
          onElementDragStart={handleElementDragStart}
          onElementDragOver={handleElementDragOver}
          onElementDrop={handleElementDrop}
          onElementDragEnd={handleElementDragEnd}
          onElementContextMenu={handleElementContextMenu}
          onSlashChange={handleTiptapSlashChange}
          slashMenu={tiptapSlashMenu}
          onMentionOpen={handleTiptapMentionOpen}
          onMentionClose={handleTiptapMentionClose}
          onMentionQuery={handleTiptapMentionQuery}
          mentionMenu={tiptapMentionMenu}
          pageSeams={pageSeams}
          mentionCandidates={mentionCandidates}
          castColors={castColors}
        />
      )}

      {mention && (
        <MentionCombobox
          candidates={activeMentionCandidates}
          query={mention.query}
          listboxId={mentionListId}
          activeIndex={mentionActive}
          position={mention.position}
          onSelect={handleTiptapMentionSelect}
          onHover={setMentionActive}
          kind={mention.kind}
          onQueryChange={handleMentionQueryChange}
          onTabAction={handleMentionTabAction}
          onClose={handleMentionPopClose}
        />
      )}

      {slash && (
        <SlashMenu
          items={slashFiltered}
          activeIndex={slashActive}
          listboxId={slashListId}
          position={slash.position}
          onSelect={applySlashTiptap}
          onHover={setSlashActive}
        />
      )}

      {hasCopilotSelection && (
        <CopilotCard
          sceneNumber={index + 1}
          selectedCount={copilotSelection.length}
          phase={copilotPhase}
          editsThisTurn={copilotEdits}
          canUndo={!!copilotInverse && copilotInverse.length > 0}
          onPolish={handlePolish}
          onUndo={handleCopilotUndo}
          instruction={copilotInstruction}
          onInstructionChange={setCopilotInstruction}
          onSubmit={handleCopilotSubmit}
          freeTextDisabled={copilotDisabled}
          summary={copilotSummary}
          failedDetail={copilotFailedDetail}
          onApply={handleCopilotApply}
          onDiscard={handleCopilotDiscard}
        />
      )}

      {dropEdge === 'after' && (
        <div className="mh-drop-indicator after" data-testid="drop-indicator" aria-hidden="true" />
      )}

      {/* Whole-scene move mode: a draggable overlay covers the block so dragging
          ANYWHERE on it moves the entire scene (not a single block). It reuses the
          existing scene-drag machinery (reorder.onDragStart + the block-level drop
          targets). Disarms on dragend/drop; Esc/outside-click handled above. */}
      {sceneMoveArmed && reorder && (
        <div
          className="mh-scene-move-overlay"
          role="button"
          aria-label={t('editor.ctxMoveScene')}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', scene.id);
            e.dataTransfer.setData(MH_DRAG_MIME, scene.id);
            reorder.onDragStart(scene.id);
          }}
          onDragEnd={() => {
            reorder.onDragEnd();
            disarmSceneMove();
          }}
          onClick={disarmSceneMove}
        >
          <span className="mh-scene-move-hint">{t('editor.moveSceneHint')}</span>
        </div>
      )}

      {contextMenu && contextMenuItems.length > 0 && (
        <SceneContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={contextMenuItems}
          onClose={closeContextMenu}
        />
      )}

      {/* Where this scene came from (3a Task 6). `scene.id` IS the registry's
          `ref_id` for `script_scene` — `screenwriting_tools` registers with
          exactly this value — so nothing is translated on the way in. A scene
          a person wrote renders nothing at all, which is the common case. */}
      {scene.id != null && String(scene.id) !== '' && (
        <OutputProvenance kind="script_scene" refId={String(scene.id)} className="mt-2" />
      )}
    </div>
  );
}
