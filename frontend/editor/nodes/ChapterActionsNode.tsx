/**
 * ChapterActionsNode — the v2 chapter card with an inline action bar (Phase B
 * Task 3). Replaces NodesView's read-only chapter card.
 *
 * Three actions — Expand / Branch / Convert to Scenes — each guarded by an
 * INLINE confirm (first click arms "Confirm?", a second within 3s executes, and
 * it auto-disarms on timeout). We deliberately do NOT reuse the legacy
 * ExpandChapterDialog: that dialog is bound to the old script canvas store, and
 * this surface keeps a clean boundary from it.
 *
 * On execute we dispatch the flat task_id endpoint (#1019 handleResponse
 * contract) and poll via `useConvertPoll` until the workflow's output appears
 * (scenes for convert, child chapters for expand/branch); the node shows a busy
 * state (aria-busy + disabled actions) until then, after which the caller
 * refreshes scenes+chapters.
 */
import { createContext, memo, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { expandChapter, createBranches } from '../../services/scriptService';
import { convertToScenes } from '../sceneService';
import type { ConvertPoll, PollPredicate } from '../useConvertPoll';
import type { ChapterNodeData } from './sceneNodeMapper';
import type { ScriptChapter } from '../../types';

/** How long an armed "Confirm?" stays live before auto-disarming. */
const CONFIRM_WINDOW_MS = 3000;
/** Length of the `ch-` id prefix the mapper emits. */
const ID_PREFIX_LEN = 3;
/** Default branch fan-out when Branch is triggered from the node (no dialog). */
const DEFAULT_BRANCH_COUNT = 2;

type ActionKind = 'expand' | 'branch' | 'convert';

const ACTION_LABEL_KEY: Record<ActionKind, string> = {
  expand: 'editor.nodesActionExpand',
  branch: 'editor.nodesActionBranch',
  convert: 'editor.nodesActionConvert',
};

/**
 * Context supplied by NodesView so the node can dispatch + poll + refresh
 * without threading callbacks through React Flow's node `data`.
 */
export interface ChapterActionContextValue {
  scriptId: string;
  /** Current chapters — used to baseline child counts for expand/branch. */
  chapters: ScriptChapter[];
  startPoll: ConvertPoll['startPoll'];
  onReload: () => void | Promise<void>;
}
export const ChapterActionContext = createContext<ChapterActionContextValue | null>(null);

function ChapterActionsNodeImpl({ id, data }: NodeProps) {
  const { t } = useTranslation();
  const ctx = useContext(ChapterActionContext);
  const d = data as unknown as ChapterNodeData;
  const chapterId = id.slice(ID_PREFIX_LEN);

  const [confirming, setConfirming] = useState<ActionKind | null>(null);
  const [busy, setBusy] = useState(false);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    },
    [],
  );

  const disarm = useCallback(() => {
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    confirmTimerRef.current = null;
    setConfirming(null);
  }, []);

  const run = useCallback(
    async (kind: ActionKind) => {
      if (!ctx) return;
      setBusy(true);
      // Baseline child chapters so expand/branch poll until a NEW one appears.
      const baselineChildren = ctx.chapters.filter(
        (c) => String(c.parent_chapter_id) === chapterId,
      ).length;
      try {
        if (kind === 'expand') {
          await expandChapter({
            script_id: ctx.scriptId,
            chapter_id: chapterId,
            title: d.title,
            summary: d.summary,
          });
        } else if (kind === 'branch') {
          await createBranches({
            script_id: ctx.scriptId,
            chapter_id: chapterId,
            title: d.title,
            summary: d.summary,
            branch_count: DEFAULT_BRANCH_COUNT,
            branch_type: 'choice',
          });
        } else {
          await convertToScenes(ctx.scriptId, chapterId);
        }
      } catch (err) {
        console.error('[ChapterActionsNode] action dispatch failed', err);
        setBusy(false);
        return;
      }
      const predicate: PollPredicate =
        kind === 'convert'
          ? (poll) => poll.scenes.some((s) => String(s.chapter_id) === chapterId)
          : (poll) =>
              poll.chapters.filter((c) => String(c.parent_chapter_id) === chapterId).length >
              baselineChildren;
      ctx.startPoll(predicate, () => {
        setBusy(false);
        void ctx.onReload();
      });
    },
    [ctx, chapterId, d.title, d.summary],
  );

  // Inline confirm: first click arms, second click (within the window) executes.
  const handleAction = useCallback(
    (kind: ActionKind) => {
      if (busy || !ctx) return;
      if (confirming === kind) {
        disarm();
        void run(kind);
        return;
      }
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      setConfirming(kind);
      confirmTimerRef.current = setTimeout(() => {
        confirmTimerRef.current = null;
        setConfirming(null);
      }, CONFIRM_WINDOW_MS);
    },
    [busy, ctx, confirming, disarm, run],
  );

  const actions: ActionKind[] = ['expand', 'branch', 'convert'];

  return (
    <div className="mh-flow-node mh-flow-chapter" aria-busy={busy || undefined}>
      <Handle type="target" position={Position.Top} className="mh-flow-handle" />
      <div className="mh-flow-chapter-head">
        {d.chapterNumber ? <span className="mh-scene-num-badge">{d.chapterNumber}</span> : null}
        <span className="mh-flow-chapter-title">
          {d.title || t('editor.nodesUntitledChapter')}
        </span>
      </div>
      {d.summary ? <div className="mh-flow-chapter-summary">{d.summary}</div> : null}
      <div className="mh-flow-actions">
        {actions.map((kind) => (
          <button
            key={kind}
            type="button"
            className={`mh-flow-action${confirming === kind ? ' confirming' : ''}`}
            disabled={busy}
            onClick={() => handleAction(kind)}
          >
            {confirming === kind ? t('editor.nodesConfirm') : t(ACTION_LABEL_KEY[kind])}
          </button>
        ))}
      </div>
      <Handle type="source" position={Position.Bottom} className="mh-flow-handle" />
    </div>
  );
}

export const ChapterActionsNode = memo(ChapterActionsNodeImpl);
