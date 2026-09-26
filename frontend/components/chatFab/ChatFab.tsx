/**
 * ChatFab — the collapsed entry of the global AI chat: the 無我 mascot,
 * draggable anywhere, snapping to the nearer edge on release.
 *
 * Behaviour lives here; the artwork is NousPup, the moods come from
 * useMascotBrain, the CSS is the `.chat-fab*` block in index.css.
 *
 * - Position: `fabSide` + `fabTop` in globalChatStore (persisted). `left`
 *   is derived from the side; both are re-clamped against the live
 *   viewport on mount and on resize, and never enter the TopBar band.
 * - Click vs drag: a press that travels less than CLICK_PX before release
 *   opens the chat; anything further is a drag and never opens.
 * - Peek: 2.5s after docking with no hover it slides half behind its edge;
 *   hover pulls it back. Chat states that need attention suppress this.
 * - Accessibility: it stays a <button>; Enter/Space open; the label carries
 *   unread count and agent state; `prefers-reduced-motion` switches every
 *   animation off (data-motion="off" for the CSS side).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

import { useGlobalChatStore } from '../../stores/globalChatStore';
import {
  FAB_SIZE_PX,
  clampFabLeft,
  clampFabTop,
  defaultFabTop,
  dockedLeft,
  isClickGesture,
  snapSide,
  type FabSide,
} from './fabGeometry';
import { NousPup } from './NousPup';
import { useMascotBrain } from './useMascotBrain';
import { usePrefersReducedMotion } from './usePrefersReducedMotion';

const SNAP_MS = 320;
const PEEK_MS = 2500;

type Phase = 'docked' | 'dragging' | 'snapping';

interface DragState {
  startX: number;
  startY: number;
  startLeft: number;
  startTop: number;
  lastX: number;
  lastY: number;
  vx: number;
  vy: number;
  moved: boolean;
  left: number;
  top: number;
}

function viewport() {
  return { vw: window.innerWidth, vh: window.innerHeight };
}

function labelFor(unread: number, activity: string): string {
  const parts = ['Open AI Chat'];
  if (unread > 0) parts.push(`${unread} new repl${unread === 1 ? 'y' : 'ies'}`);
  if (activity === 'running') parts.push('agent working');
  if (activity === 'waiting') parts.push('waiting for your answer');
  return parts.join(', ');
}

export function ChatFab(): React.ReactElement {
  const side = useGlobalChatStore((s) => s.fabSide);
  const storedTop = useGlobalChatStore((s) => s.fabTop);
  const activity = useGlobalChatStore((s) => s.fabActivity);
  const unread = useGlobalChatStore((s) => s.fabUnread);
  const setFabPosition = useGlobalChatStore((s) => s.setFabPosition);
  const setOpen = useGlobalChatStore((s) => s.setOpen);

  const reducedMotion = usePrefersReducedMotion();
  const motionOn = !reducedMotion;

  const rootRef = useRef<HTMLButtonElement>(null);
  const brain = useMascotBrain({ rootRef, motionOn, activity, unread });

  const [vp, setVp] = useState(viewport);
  const [phase, setPhase] = useState<Phase>('docked');
  const [live, setLive] = useState<{ left: number; top: number } | null>(null);
  const [peeking, setPeeking] = useState(false);
  const drag = useRef<DragState | null>(null);
  const snapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const peekTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── resting position, re-clamped against the live viewport ──
  const restingTop = storedTop == null ? defaultFabTop(vp.vh) : clampFabTop(storedTop, vp.vh);
  useEffect(() => {
    if (storedTop != null && restingTop !== storedTop) setFabPosition({ side, top: restingTop });
  }, [restingTop, setFabPosition, side, storedTop]);

  useEffect(() => {
    const onResize = () => setVp(viewport());
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ── peek: slide half behind the edge when nobody is around ──
  const clearPeek = useCallback(() => {
    if (peekTimer.current) clearTimeout(peekTimer.current);
    peekTimer.current = null;
    setPeeking(false);
  }, []);
  const schedulePeek = useCallback(() => {
    clearPeek();
    peekTimer.current = setTimeout(() => {
      const el = rootRef.current;
      if (!el || el.matches(':hover') || drag.current) return;
      setPeeking(true);
    }, PEEK_MS);
  }, [clearPeek]);

  useEffect(() => {
    if (phase !== 'docked' || brain.holdsAttention) {
      clearPeek();
      return;
    }
    schedulePeek();
    return clearPeek;
  }, [phase, brain.holdsAttention, clearPeek, schedulePeek]);

  useEffect(
    () => () => {
      if (snapTimer.current) clearTimeout(snapTimer.current);
      if (peekTimer.current) clearTimeout(peekTimer.current);
    },
    [],
  );

  // ── open ──
  const openChat = useCallback(() => {
    brain.pop(() => setOpen(true));
  }, [brain, setOpen]);

  // ── drag / snap / click ──
  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLButtonElement>) => {
      if (e.button !== 0) return;
      const el = rootRef.current;
      if (!el) return;
      clearPeek();
      brain.touch();
      const { vw } = viewport();
      const startLeft = live?.left ?? dockedLeft(side, vw);
      const startTop = live?.top ?? restingTop;
      drag.current = {
        startX: e.clientX,
        startY: e.clientY,
        startLeft,
        startTop,
        lastX: e.clientX,
        lastY: e.clientY,
        vx: 0,
        vy: 0,
        moved: false,
        left: startLeft,
        top: startTop,
      };

      const onMove = (ev: PointerEvent) => {
        const d = drag.current;
        if (!d) return;
        const dx = ev.clientX - d.startX;
        const dy = ev.clientY - d.startY;
        if (!d.moved && !isClickGesture(dx, dy)) {
          d.moved = true;
          setPhase('dragging');
          brain.dragStart();
        }
        if (!d.moved) return;
        const { vw: w, vh: h } = viewport();
        d.left = clampFabLeft(d.startLeft + dx, w);
        d.top = clampFabTop(d.startTop + dy, h);
        d.vx = d.vx * 0.6 + (ev.clientX - d.lastX) * 0.4;
        d.vy = d.vy * 0.6 + (ev.clientY - d.lastY) * 0.4;
        d.lastX = ev.clientX;
        d.lastY = ev.clientY;
        setLive({ left: d.left, top: d.top });
        brain.dragMove(d.vx, d.vy);
      };
      const onUp = () => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onUp);
        const d = drag.current;
        drag.current = null;
        if (!d) return;
        if (!d.moved) {
          openChat();
          return;
        }
        brain.dragEnd();
        const { vw: w, vh: h } = viewport();
        const nextSide: FabSide = snapSide(d.left + FAB_SIZE_PX / 2, w);
        const nextTop = clampFabTop(d.top, h);
        setFabPosition({ side: nextSide, top: nextTop });
        setLive(null);
        setPhase('snapping');
        brain.snap(nextSide);
        if (snapTimer.current) clearTimeout(snapTimer.current);
        snapTimer.current = setTimeout(
          () => {
            setPhase('docked');
            brain.land();
          },
          motionOn ? SNAP_MS : 0,
        );
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onUp);
    },
    [brain, clearPeek, live, motionOn, openChat, restingTop, setFabPosition, side],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLButtonElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        openChat();
      }
    },
    [openChat],
  );

  const left = live?.left ?? dockedLeft(side, vp.vw);
  const top = live?.top ?? restingTop;
  const docked = phase !== 'dragging';
  const moodClasses = brain.mood
    .split(' ')
    .filter(Boolean)
    .map((m) => `mood-${m}`)
    .join(' ');
  const className = [
    'chat-fab',
    docked ? `is-docked is-${side}` : '',
    phase === 'dragging' ? 'is-dragging' : '',
    phase === 'snapping' ? 'is-snapping' : '',
    peeking && docked ? 'is-peeking' : '',
    moodClasses,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <button
      ref={rootRef}
      type="button"
      onPointerDown={onPointerDown}
      onPointerEnter={() => {
        clearPeek();
        brain.hover(true);
      }}
      onPointerLeave={() => {
        brain.hover(false);
        if (phase === 'docked' && !brain.holdsAttention) schedulePeek();
      }}
      onKeyDown={onKeyDown}
      title="AI Chat (⌘I)"
      aria-label={labelFor(unread, activity)}
      data-testid="sb-toggle-chat"
      data-side={docked ? side : undefined}
      data-phase={phase}
      data-mood={brain.mood}
      data-face={brain.face}
      data-motion={motionOn ? 'on' : 'off'}
      className={className}
      style={{ left, top }}
    >
      <span className="chat-fab-body">
        <span className="chat-fab-ring" aria-hidden="true" />
        <span className="chat-fab-orb">
          <NousPup />
        </span>
        {unread > 0 && (
          <span className="chat-fab-badge" data-testid="sb-chat-unread" aria-hidden="true">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </span>
    </button>
  );
}
