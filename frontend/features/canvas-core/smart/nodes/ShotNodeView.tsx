/**
 * Shot canvas node (storyboard canvas epic, Task 3).
 *
 * Unbound (data.shot_id === null): the pre-existing hand-placed-draft card —
 * title input + reference count + notes textarea. Rendering is UNCHANGED
 * from before this task (legacy compat for canvases created before the
 * binding epic). Its head gains a "…" menu with a single "Promote to Shot"
 * entry that only FIRES an event (promoteShotBus) — Task 4 owns the actual
 * binding flow.
 *
 * Bound (data.shot_id set): the binding-mirror render — 镜号 chip, three
 * vocabulary chips (shot_type/camera_angle/camera_movement, UiSelect
 * dropdowns sharing the editor's SHOT_PARAM_VOCAB constants so the canvas
 * never drifts from the storyboard rail's option lists), a focal-length
 * chip (free-value lens string — script_ai_service.py emits arbitrary
 * strings like "35mm", so its UiSelect always includes the current value
 * even when off the common-preset list), a description textarea, a frame
 * slot (image_url or an empty-frame placeholder), and a Generate button.
 *
 * Field edits: optimistic mirror into node.data (useNodeDataPatch) +
 * `updateShot(shot_id, {field})` PATCH; a failed PATCH reverts the mirror
 * and toasts (no retry — same shape as the CharacterNodeView/PromptNodeView
 * inline-edit idiom, just with a server round-trip added). Description is
 * debounced 600ms (ShotCard.tsx parity) so every keystroke doesn't PATCH.
 *
 * Generate: dispatches through the SAME canvas generations lane the Prompt
 * node's Run uses (`dispatchGenerations`/`pollGeneration`,
 * canvasGenerationService.ts) with `node_id` = this shot node's own id —
 * NOT the shots router's `/shots/{id}/generate` (that's the editor
 * storyboard rail's own dispatch lane). `canvas_generation_workflow`
 * backfills `script_shots.image_url`/`status` server-side when the target
 * node is a bound shot (canvas subsystem facts §7 lane (c)); this view
 * still has to patch its OWN node mirror on completion — the backfill step
 * writes the DB row, not `canvases.nodes_json`.
 *
 * genResume note (fix-round-2, supersedes round-1): `handleGenerate`
 * persists `data.gen_task_id` the instant dispatch returns and clears it
 * once the task settles. `genResume.ts`'s `resumePendingGenerations` (which
 * fires on canvas load AND on every in-app nav back into an already-
 * mounted canvas, since this component's own dispatch→poll chain is never
 * cancelled by an unmount) re-attaches polling by that task id — the SAME
 * treatment a prompt node's `gen_tasks` batch gets, singular here. Only a
 * shot with NO persisted task id (crashed in the narrow dispatch-request
 * window) falls back to a local-mirror reconcile: `image_url` already
 * landed → 'done' (don't lie that it failed); still null → 'failed' so
 * Generate re-enables and becomes retryable. Round-1's mistake: reconciling
 * from `image_url` alone raced a still-live dispatch/poll chain from before
 * a nav-away, occasionally flipping an in-flight generation to 'failed'
 * while it was still genuinely running — re-review caught it before it
 * shipped past this branch.
 *
 * Stale (Task 4, shotSync): `data.stale` is set by `reconcileShotNodes` when
 * this node's `shot_id` no longer resolves (the shot was deleted from the
 * storyboard list elsewhere). Grey render, every input disabled, no "…"
 * menu (nothing left to promote or navigate to) — the node's only remaining
 * lifecycle action is the user's own React Flow delete, which the
 * orchestration layer also performs automatically at the next autosave
 * (`canvasCoreStore.ts`'s `doSave`).
 */
import { mediaSrc } from '../mediaUrl';
import { useCallback, useEffect, useRef, useState, type ChangeEvent } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { useTranslation } from 'react-i18next';
import { ImageOff, ListX, MoreVertical, Sparkles } from 'lucide-react';

import type { ShotNodeData } from '../types';
import { SMART_NODE_DEFAULT_WIDTH } from '../types';
import { useCanvasReadOnly } from './useCanvasReadOnly';
import { useNodeDataPatch } from './useNodeDataPatch';
import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import { UiSelect } from '../../../../components/ui';
import { useOptionalToast } from '../../../../components/Toast';
import { updateShot, type ShotInput } from '../../../../editor/sceneService';
import {
  SHOT_TYPES,
  CAMERA_ANGLES,
  CAMERA_MOVEMENTS,
} from '../shotVocab';
import { dispatchGenerations, pollGeneration } from '../../services/canvasGenerationService';
import { requestPromoteShot } from '../promoteShotBus';
import { requestOpenShotInList } from '../openShotInListBus';
import { registerActiveShotPoll, unregisterActiveShotPoll } from '../genResume';
import { OutputProvenance } from '../../../../components/agentActivity/OutputProvenance';

const CANVAS_CHIP =
  'nodrag rounded-full border border-canvas-line bg-transparent px-2 py-0.5 text-[11px] text-canvas-text hover:border-canvas-strong/50 focus-visible:ring-1 focus-visible:ring-canvas-strong/40';

/** Description debounce window (ShotCard.tsx parity). */
const DESCRIPTION_DEBOUNCE_MS = 600;

/** Common lens-length presets. Free-value field (no server-side vocab) —
 *  the current value is always unioned in below so an AI-produced or
 *  hand-typed off-list value still renders instead of silently vanishing. */
const FOCAL_LENGTH_PRESETS = ['14mm', '16mm', '24mm', '35mm', '50mm', '85mm', '135mm', '200mm'];

type ChipField = 'shot_type' | 'camera_angle' | 'camera_movement';

const CHIP_VOCAB: Record<ChipField, readonly string[]> = {
  shot_type: SHOT_TYPES,
  camera_angle: CAMERA_ANGLES,
  camera_movement: CAMERA_MOVEMENTS,
};

const CHIP_LABEL: Record<ChipField, string> = {
  shot_type: 'Shot type',
  camera_angle: 'Camera angle',
  camera_movement: 'Camera movement',
};

export function ShotNodeView({ id, data, selected }: NodeProps) {
  const {
    title,
    reference_resource_ids,
    notes,
    shot_id = null,
    shot_label = null,
    shot_type = null,
    camera_angle = null,
    camera_movement = null,
    focal_length = null,
    description = null,
    image_url = null,
    shot_status = null,
    stale = false,
  } = data as unknown as ShotNodeData;
  const { t } = useTranslation();
  const patch = useNodeDataPatch(id);
  const toast = useOptionalToast();
  const canvasId = useCanvasCoreStore((s) => s.canvasId);
  /**
   * Read-only session — the storyboard canvas is the case this actually
   * happens on in production (a viewer-role member opening an episode's
   * storyboard). Every write affordance goes: Generate (its POST is gated
   * by `_gate_canvas_write`, so it would 403), Promote to Shot (it creates
   * a `script_shots` row), and — added here — the inline field edits. The
   * unbound card's title/notes patch the canvas document; the bound card's
   * chips/description PATCH `script_shots` through `updateShot`, which the
   * server refuses too. Both were still typeable after #1828: the text
   * appeared, then vanished on reload. "Open in list" stays — it only
   * navigates.
   */
  const readOnly = useCanvasReadOnly();
  const bound = shot_id != null;

  const [menuOpen, setMenuOpen] = useState(false);

  // ---- Bound-field edit: optimistic mirror + PATCH script_shots, revert on
  // failure (no retry). `shot_id` is captured per-call so a rapid unmount
  // mid-request can't PATCH the wrong id. ----
  const commitField = useCallback(
    (field: keyof ShotInput, next: string | null, previous: string | null) => {
      // Task 4: a stale node's shot_id is a dangling reference — PATCHing
      // it would 404. Inputs are also `disabled` below; this is the
      // belt-and-suspenders guard for any path that bypasses that.
      if (!shot_id || stale) return;
      patch({ [field]: next });
      updateShot(shot_id, { [field]: next } as ShotInput).catch((err: unknown) => {
        console.error('[ShotNodeView] updateShot failed:', field, err);
        patch({ [field]: previous });
        toast?.addToast(t('canvas.shotNode.patchFailed'), 'error');
      });
    },
    [shot_id, stale, patch, toast, t],
  );

  const handleChipChange = useCallback(
    (field: ChipField, current: string | null) => (e: ChangeEvent<HTMLSelectElement>) => {
      const next = e.target.value || null;
      if (next === current) return;
      commitField(field, next, current);
    },
    [commitField],
  );

  const handleFocalChange = useCallback(
    (e: ChangeEvent<HTMLSelectElement>) => {
      const next = e.target.value || null;
      if (next === focal_length) return;
      commitField('focal_length', next, focal_length);
    },
    [commitField, focal_length],
  );

  // Description: local state + debounced commit (ShotCard.tsx parity) — an
  // inbound prop change (reconcile on canvas open) re-syncs only while the
  // user isn't mid-edit.
  const [desc, setDesc] = useState(description ?? '');
  const editedRef = useRef(false);
  useEffect(() => {
    if (!editedRef.current) setDesc(description ?? '');
  }, [description]);
  useEffect(() => {
    if (!editedRef.current) return;
    const timer = setTimeout(() => {
      editedRef.current = false;
      if (desc !== (description ?? '')) {
        commitField('description', desc || null, description);
      }
    }, DESCRIPTION_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only on `desc`; commitField/description read fresh via closure re-creation each render
  }, [desc]);
  const handleDescChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    editedRef.current = true;
    setDesc(e.target.value);
  }, []);

  // ---- Generate: same service/endpoint as the Prompt node's Run
  // (canvasGenerationService.ts), target node_id = this shot node.
  //
  // fix-round-2: this dispatch→poll→patch chain is a free-floating async
  // closure — nothing cancels it if the user navigates away mid-poll (React
  // doesn't cancel promises on unmount), and CanvasPage re-fires
  // `resumePendingGenerations` on every loadStatus→'ready', including
  // in-app nav BACK into the same still-mounted canvas. Two disciplines
  // fix the resulting race:
  //  1. Persist `gen_task_id` the instant dispatch returns (prompt node's
  //     `gen_tasks`-at-dispatch-time parity) so a concurrent reconcile
  //     re-attaches polling instead of guessing 'failed' out from under a
  //     live chain (genResume.ts's `resumeShotTask`).
  //  2. Every patch after an `await` is gated by `sameCanvas()` (loopRun/
  //     regenerate.ts discipline) — a canvas switch mid-flight must not
  //     land a stale patch onto whatever node now holds this id.
  //
  // fix-round-3: (1) alone isn't enough — a reconcile pass re-attaching by
  // task id would attach a SECOND poller alongside THIS closure (still
  // alive, never cancelled). Register the task id in `activeShotPolls` the
  // instant it's known so `resumeShotTask` sees this closure already owns
  // it and skips instead of racing it; always release in `finally` so a
  // later reconcile isn't blocked forever once this closure is done with
  // the id (success, failure, or thrown).
  const generating = shot_status === 'generating';
  const handleGenerate = useCallback(() => {
    // Task 4: a stale node's shot_id no longer resolves server-side —
    // dispatching against it would 404 the moment the workflow tries to
    // backfill script_shots.
    if (!shot_id || generating || stale) return;
    const startCanvasId = useCanvasCoreStore.getState().canvasId;
    if (!startCanvasId) return;
    const sameCanvas = () => useCanvasCoreStore.getState().canvasId === startCanvasId;
    const guardedPatch = (fields: Record<string, unknown>) => {
      if (sameCanvas()) patch(fields);
    };
    guardedPatch({ shot_status: 'generating' });
    void (async () => {
      let taskId: string | null = null;
      try {
        const taskIds = await dispatchGenerations(startCanvasId, {
          node_id: id,
          kind: 'image',
          prompt: description || title || '',
          count: 1,
        });
        taskId = taskIds[0];
        // Claim the task id BEFORE polling — a reconcile pass firing
        // between dispatch and this line is the one window where it
        // could still race in (registerActiveShotPoll is synchronous, no
        // await between it and the assignment above).
        registerActiveShotPoll(startCanvasId, taskId);
        // Persist BEFORE polling — a nav-away between dispatch and this
        // patch landing is the one genuinely-unrecoverable window (see the
        // no-task-id branch in genResume.ts), so it must be as narrow as
        // possible.
        guardedPatch({ gen_task_id: taskId });
        const task = await pollGeneration(taskId);
        const url = task.phase === 'completed' ? (task.metadata?.result_url ?? null) : null;
        if (url) {
          guardedPatch({ image_url: url, shot_status: 'done', gen_task_id: null });
        } else {
          guardedPatch({ shot_status: 'failed', gen_task_id: null });
          if (sameCanvas()) toast?.addToast(t('canvas.shotNode.generateFailed'), 'error');
        }
      } catch (err) {
        console.error('[ShotNodeView] generate failed:', err);
        guardedPatch({ shot_status: 'failed', gen_task_id: null });
        if (sameCanvas()) toast?.addToast(t('canvas.shotNode.generateFailed'), 'error');
      } finally {
        if (taskId) unregisterActiveShotPoll(startCanvasId, taskId);
      }
    })();
  }, [shot_id, generating, stale, id, description, title, patch, toast, t]);

  const focalOptions = focal_length && !FOCAL_LENGTH_PRESETS.includes(focal_length)
    ? [focal_length, ...FOCAL_LENGTH_PRESETS]
    : FOCAL_LENGTH_PRESETS;

  return (
    <div
      data-testid="smart-shot-node"
      className={`mh-node border-canvas-line ${selected ? 'mh-node-selected' : ''} ${
        stale ? 'opacity-60 grayscale' : ''
      }`}
      style={{ width: SMART_NODE_DEFAULT_WIDTH.shot }}
    >
      {/* Shot is a SOURCE card (canConnectSmart: `* → shot` is disallowed) —
          no target handle, matching the pre-existing unbound render. */}
      <div className="mh-node-head">
        <div className="mh-node-title flex items-center gap-1.5">
          Shot
          {bound && shot_label && (
            <span
              data-testid="shot-node-label"
              className="mh-accent-chip rounded-full px-1.5 py-0.5 text-[10px]"
            >
              {shot_label}
            </span>
          )}
        </div>
        {/* Task 4: no menu once stale — there's nothing left to promote
            (already bound) or navigate to (the shot row is gone); the
            node's only remaining lifecycle action is the user's own
            React Flow delete (Delete key / multi-select), which needs no
            menu entry. */}
        {!stale && (
          <div className="relative">
            <button
              type="button"
              className="nodrag rounded p-0.5 text-canvas-muted hover:text-canvas-text"
              aria-label={t('canvas.shotNode.menu')}
              data-testid="shot-node-menu-trigger"
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MoreVertical size={14} />
            </button>
            {menuOpen && (
              <div
                className="absolute right-0 top-full z-10 mt-1 min-w-[9.5rem] rounded-lg border border-canvas-line bg-canvas-surface py-1 shadow-lg"
                data-testid="shot-node-menu"
              >
                {/* Promote creates a `script_shots` row, so it is withheld
                    from a read-only session; "Open in list" below only
                    navigates and stays available to everyone. */}
                {!bound ? (
                  readOnly ? null : (
                    <button
                      type="button"
                      className="nodrag flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs text-canvas-text hover:bg-canvas-line/30"
                      data-testid="shot-node-promote"
                      onClick={() => {
                        setMenuOpen(false);
                        requestPromoteShot(id);
                      }}
                    >
                      <Sparkles size={12} />
                      {t('canvas.shotNode.promote')}
                    </button>
                  )
                ) : (
                  <button
                    type="button"
                    className="nodrag flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-xs text-canvas-text hover:bg-canvas-line/30"
                    data-testid="shot-node-open-in-list"
                    onClick={() => {
                      setMenuOpen(false);
                      if (shot_id) requestOpenShotInList(shot_id);
                    }}
                  >
                    <ListX size={12} />
                    {t('canvas.shotNode.deleteInList')}
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {stale && (
        <div
          data-testid="shot-node-stale-banner"
          className="mx-3 mb-1 rounded-md border border-dashed border-canvas-line px-2 py-1 text-[10px] text-canvas-muted"
        >
          {t('canvas.shotNode.stale')}
        </div>
      )}

      {!bound ? (
        <div className="p-3">
          <input
            // nodrag = React Flow does not start a node drag from this input
            className="nodrag w-full bg-transparent text-sm font-medium text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            placeholder="Shot title"
            value={title}
            onChange={(e) => patch({ title: e.target.value })}
            aria-label="Shot title"
            readOnly={readOnly}
          />
          {reference_resource_ids.length > 0 && (
            <div className="mt-1 text-xs text-canvas-muted">
              {reference_resource_ids.length} reference
              {reference_resource_ids.length === 1 ? '' : 's'}
            </div>
          )}
          <textarea
            // nowheel = wheel events don't pan the canvas while scrolling the textarea
            className="nodrag nowheel mt-2 w-full resize-none bg-transparent text-xs text-canvas-muted outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            placeholder="Notes (optional)"
            rows={3}
            value={notes}
            onChange={(e) => patch({ notes: e.target.value })}
            aria-label="Shot notes"
            readOnly={readOnly}
          />
        </div>
      ) : (
        <div className="p-3">
          <div className="flex flex-wrap items-center gap-1" data-testid="shot-node-chips">
            {(Object.keys(CHIP_VOCAB) as ChipField[]).map((field) => {
              const current = { shot_type, camera_angle, camera_movement }[field];
              return (
                <UiSelect
                  key={field}
                  triggerClassName={CANVAS_CHIP}
                  value={current ?? ''}
                  onChange={handleChipChange(field, current)}
                  aria-label={CHIP_LABEL[field]}
                  disabled={stale || readOnly}
                >
                  <option value="">{t('canvas.shotNode.unset')}</option>
                  {CHIP_VOCAB[field].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </UiSelect>
              );
            })}
            <UiSelect
              triggerClassName={CANVAS_CHIP}
              value={focal_length ?? ''}
              onChange={handleFocalChange}
              aria-label="Focal length"
              disabled={stale || readOnly}
            >
              <option value="">{t('canvas.shotNode.unset')}</option>
              {focalOptions.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </UiSelect>
          </div>

          <textarea
            className="nodrag nowheel mt-2 w-full resize-none bg-transparent text-xs text-canvas-text outline-none placeholder:text-canvas-muted focus:ring-1 focus:ring-canvas-strong/40 read-only:opacity-80 read-only:cursor-default"
            placeholder="Shot description"
            rows={2}
            value={desc}
            onChange={handleDescChange}
            aria-label="Shot description"
            disabled={stale}
            readOnly={readOnly}
          />

          <div className="mt-2 aspect-video w-full overflow-hidden rounded-lg border border-canvas-line bg-canvas-line/10">
            {image_url ? (
              <img
                data-testid="shot-node-frame"
                src={mediaSrc(image_url)}
                alt=""
                className="h-full w-full object-cover"
              />
            ) : (
              <div
                data-testid="shot-node-frame-empty"
                className="flex h-full w-full flex-col items-center justify-center gap-1 text-canvas-muted"
              >
                <ImageOff size={18} />
                <span className="text-[10px]">{t('canvas.shotNode.frameEmpty')}</span>
              </div>
            )}
          </div>

          <button
            type="button"
            className="nodrag mh-chip mt-2 w-full justify-center"
            data-testid="shot-node-generate"
            disabled={generating || !canvasId || stale || readOnly}
            onClick={handleGenerate}
          >
            {generating ? t('canvas.shotNode.generating') : t('canvas.generate')}
          </button>

          {/* Where this shot came from (3a Task 6). `shot_id` IS the registry's
              `ref_id` for `script_shot` — `screenwriting_tools` registers with
              exactly this value — so no translation is needed. Renders nothing
              at all for a shot a person wrote, which is most of them. */}
          {shot_id && (
            <OutputProvenance kind="script_shot" refId={String(shot_id)} className="nodrag mt-2" />
          )}
        </div>
      )}

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
