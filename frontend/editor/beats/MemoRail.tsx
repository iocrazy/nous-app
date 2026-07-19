/**
 * MemoRail — the Beats M4 timeline memo-pin rail (laper-aligned).
 *
 * A second ruler line below the arrangement cards where inspiration-library
 * notes anchored to this script hang as memo pins: a dot on the ruler, a relaxed
 * SVG curve, and a floating card. Hovering the ruler snaps a green "+" to the
 * grid; clicking it opens a quick-capture card that creates an anchored note.
 * Pins drag along the ruler to re-time (pointer capture, local-only during the
 * drag, one PATCH on pointer-up — the M2 drag discipline, #1389); a click that
 * doesn't move opens the memo for editing.
 *
 * Pins inherit the colour of the beat they sit within (pinColorFor); cards that
 * would overlap horizontally stagger into rows (layoutMemoPins). Both are pure
 * (memoGeometry). This component owns data + pointer wiring; it self-fetches its
 * anchored notes so the parent stays a pure timeline shell.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../components/Toast';
import {
  attachmentUrlWithToken,
  createNote,
  deleteNote,
  listAnchoredNotes,
  updateNote,
  uploadAttachment,
  type InspirationNote,
} from '../../services/inspirationService';
import type { Beat } from '../sceneService';
import { pxToTime, snapSec, timeToPx } from './arrangementGeometry';
import { formatAnchorSec, layoutMemoPins, memoPinPath, pinColorFor } from './memoGeometry';
import { MemoQuickCard } from './MemoQuickCard';

interface Props {
  scriptId: string;
  beats: Beat[];
  pxPerSec: number;
  granularity: number;
  canvasWidth: number;
  /** Major-tick seconds for the rail baseline marks. */
  majorTickSecs: number[];
}

// Rail geometry (px) — local coords within the rail root (position:relative).
const RULER_ZONE = 26; // top strip: baseline + dots + hover "+"
const DOT_Y = 9; // dot centre y (on the ruler line)
const CARD_TOP = 52; // first card row's top y
const CARD_W = 212;
const CARD_H = 78; // stacking height (visual card can be shorter)
const CARD_GAP = 10;
const NEUTRAL = 'var(--ink-faint)';

interface DragCtx {
  id: string;
  pointerId: number;
  originClientX: number;
  origSec: number;
  sec: number;
  changed: boolean;
}

type Panel =
  | { mode: 'create'; sec: number }
  | { mode: 'edit'; note: InspirationNote }
  | null;

/** Crude markdown→preview: collapse whitespace, drop the commonest marks. */
function previewText(md: string): string {
  return md
    .replace(/[#>*_`~-]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 90);
}

export function MemoRail({
  scriptId,
  beats,
  pxPerSec,
  granularity,
  canvasWidth,
  majorTickSecs,
}: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const { mediaToken } = useAuth();

  const [memos, setMemos] = useState<InspirationNote[]>([]);
  const [hoverSec, setHoverSec] = useState<number | null>(null);
  const [drag, setDrag] = useState<DragCtx | null>(null);
  const [panel, setPanel] = useState<Panel>(null);
  const [busy, setBusy] = useState(false);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragCtx | null>(null);
  // Toast + t via refs so `reload` (and the mount effect that depends on it)
  // keep a stable identity across renders — otherwise a fresh `t` each render
  // (e.g. under a test i18n mock) would re-fire the load effect on a loop.
  const addToastRef = useRef(addToast);
  addToastRef.current = addToast;
  const tRef = useRef(t);
  tRef.current = t;

  const reload = useCallback(async () => {
    try {
      const rows = await listAnchoredNotes(scriptId);
      setMemos(rows.filter((r) => r.anchor_sec != null));
    } catch (err) {
      console.error('[MemoRail] load failed', err);
      addToastRef.current(tRef.current('editor.memoLoadFailed'), 'error');
    }
  }, [scriptId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Effective offset folds an in-flight drag over the stored anchor.
  const secOf = useCallback(
    (memo: InspirationNote): number =>
      drag && drag.id === memo.id ? drag.sec : (memo.anchor_sec ?? 0),
    [drag],
  );

  const localX = useCallback((clientX: number): number => {
    const rect = rootRef.current?.getBoundingClientRect();
    return rect ? clientX - rect.left : 0;
  }, []);

  // ── Card layout (rows) ─────────────────────────────────────────────────────
  const boxes = useMemo(
    () =>
      memos.map((m) => {
        const pinX = timeToPx(secOf(m), pxPerSec);
        const x = Math.max(0, Math.min(canvasWidth - CARD_W, pinX - CARD_W / 2));
        return { id: m.id, x, width: CARD_W, pinX };
      }),
    [memos, secOf, pxPerSec, canvasWidth],
  );
  const rows = useMemo(() => layoutMemoPins(boxes, CARD_GAP), [boxes]);
  const rowCount = useMemo(() => {
    let max = -1;
    for (const r of rows.values()) if (r > max) max = r;
    return max + 1;
  }, [rows]);
  const railHeight = Math.max(120, CARD_TOP + rowCount * (CARD_H + CARD_GAP));
  const boxById = useMemo(() => new Map(boxes.map((b) => [b.id, b])), [boxes]);

  // ── Hover "+" affordance ───────────────────────────────────────────────────
  const onHoverMove = useCallback(
    (e: React.PointerEvent) => {
      if (dragRef.current) return;
      setHoverSec(snapSec(pxToTime(localX(e.clientX), pxPerSec), granularity));
    },
    [localX, pxPerSec, granularity],
  );

  // Click anywhere on the rail → open the create card at the snapped instant.
  // Computed from the click position (not hover state) so it is robust to the
  // pointer moving between hover and click.
  const openCreateAt = useCallback(
    (clientX: number) => {
      if (dragRef.current) return;
      setPanel({
        mode: 'create',
        sec: snapSec(pxToTime(localX(clientX), pxPerSec), granularity),
      });
    },
    [localX, pxPerSec, granularity],
  );

  // ── Pin drag (move) / click (edit) ─────────────────────────────────────────
  const beginDrag = useCallback(
    (e: React.PointerEvent, memo: InspirationNote) => {
      if (e.button !== 0 || dragRef.current) return;
      (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
      const ctx: DragCtx = {
        id: memo.id,
        pointerId: e.pointerId,
        originClientX: e.clientX,
        origSec: memo.anchor_sec ?? 0,
        sec: memo.anchor_sec ?? 0,
        changed: false,
      };
      dragRef.current = ctx;
      setDrag(ctx);
      e.stopPropagation();
      e.preventDefault();
    },
    [],
  );

  const moveDrag = useCallback(
    (e: React.PointerEvent) => {
      const ctx = dragRef.current;
      if (!ctx || e.pointerId !== ctx.pointerId) return;
      const dSec = pxToTime(e.clientX - ctx.originClientX, pxPerSec);
      const next = snapSec(ctx.origSec + dSec, granularity);
      if (next === ctx.sec) return;
      ctx.sec = next;
      ctx.changed = true;
      setDrag({ ...ctx });
    },
    [pxPerSec, granularity],
  );

  const endDrag = useCallback(
    (e: React.PointerEvent, memo: InspirationNote) => {
      const ctx = dragRef.current;
      if (!ctx || e.pointerId !== ctx.pointerId) return;
      (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
      dragRef.current = null;
      const moved = ctx.changed;
      const finalSec = ctx.sec;
      setDrag(null);
      if (!moved) {
        setPanel({ mode: 'edit', note: memo });
        return;
      }
      // Optimistic re-time; PATCH once, re-pull on failure.
      setMemos((prev) =>
        prev.map((m) => (m.id === memo.id ? { ...m, anchor_sec: finalSec } : m)),
      );
      updateNote(memo.id, { anchor_sec: finalSec }).catch((err) => {
        console.error('[MemoRail] move failed', err);
        addToast(t('editor.memoMoveFailed'), 'error');
        void reload();
      });
    },
    [addToast, t, reload],
  );

  // ── Quick-card actions ─────────────────────────────────────────────────────
  const publishCreate = useCallback(
    async (content: string, files: File[]) => {
      if (panel?.mode !== 'create') return;
      setBusy(true);
      try {
        const note = await createNote(content, undefined, {
          scriptId,
          sec: panel.sec,
        });
        for (const f of files) await uploadAttachment(note.id, f);
        setPanel(null);
        await reload();
      } catch (err) {
        console.error('[MemoRail] publish failed', err);
        addToast(t('editor.memoPublishFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [panel, scriptId, reload, addToast, t],
  );

  const saveEdit = useCallback(
    async (content: string, files: File[]) => {
      if (panel?.mode !== 'edit') return;
      const id = panel.note.id;
      setBusy(true);
      try {
        await updateNote(id, { content_md: content });
        for (const f of files) await uploadAttachment(id, f);
        setPanel(null);
        await reload();
      } catch (err) {
        console.error('[MemoRail] save failed', err);
        addToast(t('editor.memoSaveFailed'), 'error');
      } finally {
        setBusy(false);
      }
    },
    [panel, reload, addToast, t],
  );

  const unpin = useCallback(async () => {
    if (panel?.mode !== 'edit') return;
    const id = panel.note.id;
    setPanel(null);
    setMemos((prev) => prev.filter((m) => m.id !== id));
    try {
      await updateNote(id, { anchor_script_id: null, anchor_sec: null });
    } catch (err) {
      console.error('[MemoRail] unpin failed', err);
      addToast(t('editor.memoUnpinFailed'), 'error');
      void reload();
    }
  }, [panel, reload, addToast, t]);

  const removeMemo = useCallback(async () => {
    if (panel?.mode !== 'edit') return;
    const id = panel.note.id;
    setPanel(null);
    setMemos((prev) => prev.filter((m) => m.id !== id));
    try {
      await deleteNote(id);
    } catch (err) {
      console.error('[MemoRail] delete failed', err);
      addToast(t('editor.memoDeleteFailed'), 'error');
      void reload();
    }
  }, [panel, reload, addToast, t]);

  const panelX = useMemo(() => {
    if (!panel) return 0;
    const sec = panel.mode === 'create' ? panel.sec : (panel.note.anchor_sec ?? 0);
    const pinX = timeToPx(sec, pxPerSec);
    return Math.max(0, Math.min(canvasWidth - 272, pinX - 136));
  }, [panel, pxPerSec, canvasWidth]);

  return (
    <div
      className="mh-memo-rail"
      data-testid="memo-rail"
      ref={rootRef}
      style={{ width: `${canvasWidth}px`, height: `${railHeight}px` }}
    >
      <div className="mh-memo-ruler-line" aria-hidden="true" />

      {/* Baseline major-tick marks. */}
      {majorTickSecs.map((sec) => (
        <div
          key={sec}
          className="mh-memo-tick"
          aria-hidden="true"
          style={{ left: `${timeToPx(sec, pxPerSec)}px` }}
        />
      ))}

      {/* Hover/click strip: snaps a "+" to the grid, opens the create card. */}
      <div
        className="mh-memo-hitzone"
        data-testid="memo-hitzone"
        style={{ height: `${RULER_ZONE}px` }}
        onPointerMove={onHoverMove}
        onPointerLeave={() => setHoverSec(null)}
        onClick={(e) => openCreateAt(e.clientX)}
      />

      {/* Hover hint only — pointer-events:none, so the click falls through to the
          hitzone (which opens the create card at the clicked instant). */}
      {hoverSec != null && !drag && (
        <span
          className="mh-memo-add"
          data-testid="memo-add"
          aria-hidden="true"
          style={{ left: `${timeToPx(hoverSec, pxPerSec)}px`, top: `${DOT_Y}px` }}
        >
          +
        </span>
      )}

      {/* Connector curves (behind dots + cards). */}
      <svg className="mh-memo-links" width={canvasWidth} height={railHeight} aria-hidden="true">
        {memos.map((m) => {
          const box = boxById.get(m.id);
          if (!box) return null;
          const row = rows.get(m.id) ?? 0;
          const cardTop = CARD_TOP + row * (CARD_H + CARD_GAP);
          const color = pinColorFor(secOf(m), beats) ?? NEUTRAL;
          return (
            <path
              key={m.id}
              className="mh-memo-link"
              d={memoPinPath(box.pinX, DOT_Y, box.x + CARD_W / 2, cardTop)}
              stroke={color}
            />
          );
        })}
      </svg>

      {/* Pin dots (draggable). */}
      {memos.map((m) => {
        const color = pinColorFor(secOf(m), beats) ?? NEUTRAL;
        return (
          <button
            type="button"
            key={m.id}
            className={`mh-memo-dot${drag?.id === m.id ? ' dragging' : ''}`}
            data-testid="memo-dot"
            data-note-id={m.id}
            aria-label={t('editor.memoEdit')}
            style={{
              left: `${timeToPx(secOf(m), pxPerSec)}px`,
              top: `${DOT_Y}px`,
              background: color,
            }}
            onPointerDown={(e) => beginDrag(e, m)}
            onPointerMove={moveDrag}
            onPointerUp={(e) => endDrag(e, m)}
            onPointerCancel={(e) => endDrag(e, m)}
          />
        );
      })}

      {/* Floating memo cards. */}
      {memos.map((m) => {
        const box = boxById.get(m.id);
        if (!box) return null;
        const row = rows.get(m.id) ?? 0;
        const cardTop = CARD_TOP + row * (CARD_H + CARD_GAP);
        const image = m.attachments?.find((a) => a.mime.startsWith('image/'));
        return (
          <div
            key={m.id}
            className="mh-memo-card"
            data-testid="memo-card"
            data-note-id={m.id}
            style={{ left: `${box.x}px`, top: `${cardTop}px`, width: `${CARD_W}px` }}
          >
            <div className="mh-memo-card-head">
              <span className="mh-memo-card-chip">{formatAnchorSec(secOf(m))}</span>
              <button
                type="button"
                className="mh-memo-card-edit"
                data-testid="memo-card-edit"
                onClick={() => setPanel({ mode: 'edit', note: m })}
              >
                {t('editor.memoEdit')}
              </button>
            </div>
            {image && (
              <img
                className="mh-memo-card-thumb"
                src={attachmentUrlWithToken(image.id, mediaToken ?? undefined)}
                alt={image.original_name}
              />
            )}
            <div className="mh-memo-card-body">{previewText(m.content_md)}</div>
          </div>
        );
      })}

      {panel && (
        <MemoQuickCard
          mode={panel.mode}
          sec={panel.mode === 'create' ? panel.sec : (panel.note.anchor_sec ?? 0)}
          x={panelX}
          initialContent={panel.mode === 'edit' ? panel.note.content_md : ''}
          attachments={panel.mode === 'edit' ? panel.note.attachments : []}
          busy={busy}
          onPublish={publishCreate}
          onSave={saveEdit}
          onUnpin={unpin}
          onDelete={removeMemo}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  );
}
