/**
 * FloatingChatWidget — the global AI chat shell (replaces AIChatDrawer).
 *
 * Collapsed: a bottom-right FAB on every page. Expanded: a floating window
 * that can be dragged by its title bar and resized from the top/left edges
 * and the top-left corner (it is anchored bottom-right, so growth goes
 * up/left). Rect + open state persist via globalChatStore, which also lets
 * the window survive navigation between AppLayout routes and the fullscreen
 * editor routes (both mount this widget; only one host renders at a time).
 *
 * Core chat UI is the existing AIChatPanel — untouched. Route awareness is
 * "sense, don't interrupt": the title bar shows the current module chip
 * (from pageContext); NEW sessions created inside the panel pick up the
 * live contextType/contextId props automatically, but an ongoing session
 * is never switched away on navigation.
 *
 * Keyboard: ⌘I / Ctrl+I toggles, ESC minimizes.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { GripHorizontal, MessageSquare, Minus } from 'lucide-react';

import { AIChatPanel } from './AIChatPanel';
import {
  CHAT_MAX_W,
  CHAT_MIN_H,
  CHAT_MIN_W,
  useGlobalChatStore,
} from '../stores/globalChatStore';

type ResizeMode = 'nw' | 'n' | 'w';

export function FloatingChatWidget(): React.ReactElement {
  const open = useGlobalChatStore((s) => s.open);
  const right = useGlobalChatStore((s) => s.right);
  const bottom = useGlobalChatStore((s) => s.bottom);
  const width = useGlobalChatStore((s) => s.width);
  const height = useGlobalChatStore((s) => s.height);
  const pageContext = useGlobalChatStore((s) => s.pageContext);
  const setOpen = useGlobalChatStore((s) => s.setOpen);
  const toggle = useGlobalChatStore((s) => s.toggle);
  const setRect = useGlobalChatStore((s) => s.setRect);

  const minimize = useCallback(() => setOpen(false), [setOpen]);
  // Laper-style Chat History: the title-bar grip button slides the session
  // list out from the left edge of the window.
  const [sessionsOpen, setSessionsOpen] = useState(false);

  // Keyboard: ⌘I / Ctrl+I toggle, ESC minimize.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && useGlobalChatStore.getState().open) {
        e.preventDefault();
        minimize();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [minimize, toggle]);

  // ── Drag + resize via pointer capture ────────────────────────────────
  // Pointer capture routes ALL move/up events to the element that captured
  // (survives leaving the element, iframes, other overlays) — far more
  // reliable than window listeners, which weren't moving the window.
  const dragState = useRef<{
    startX: number;
    startY: number;
    startRight: number;
    startBottom: number;
  } | null>(null);

  const onDragDown = useCallback((e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button')) return; // buttons stay clickable
    e.preventDefault();
    const s = useGlobalChatStore.getState();
    dragState.current = {
      startX: e.clientX,
      startY: e.clientY,
      startRight: s.right,
      startBottom: s.bottom,
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, []);

  const onDragMove = useCallback(
    (e: React.PointerEvent) => {
      const d = dragState.current;
      if (!d) return;
      const { width: w, height: h } = useGlobalChatStore.getState();
      setRect({
        right: clamp(
          d.startRight - (e.clientX - d.startX),
          8,
          Math.max(8, window.innerWidth - w - 8),
        ),
        bottom: clamp(
          d.startBottom - (e.clientY - d.startY),
          8,
          Math.max(8, window.innerHeight - h - 8),
        ),
      });
    },
    [setRect],
  );

  const endDrag = useCallback((e: React.PointerEvent) => {
    dragState.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* pointer already released */
    }
  }, []);

  const resizeState = useRef<{
    mode: ResizeMode;
    startX: number;
    startY: number;
    startW: number;
    startH: number;
  } | null>(null);

  const onResizeDown = useCallback(
    (mode: ResizeMode) => (e: React.PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const s = useGlobalChatStore.getState();
      resizeState.current = {
        mode,
        startX: e.clientX,
        startY: e.clientY,
        startW: s.width,
        startH: s.height,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    },
    [],
  );

  const onResizeMove = useCallback(
    (e: React.PointerEvent) => {
      const r = resizeState.current;
      if (!r) return;
      const dx = e.clientX - r.startX;
      const dy = e.clientY - r.startY;
      const next: { width?: number; height?: number } = {};
      if (r.mode === 'nw' || r.mode === 'w') {
        next.width = clamp(r.startW - dx, CHAT_MIN_W, CHAT_MAX_W);
      }
      if (r.mode === 'nw' || r.mode === 'n') {
        next.height = clamp(r.startH - dy, CHAT_MIN_H, window.innerHeight - 48);
      }
      setRect(next);
    },
    [setRect],
  );

  const endResize = useCallback((e: React.PointerEvent) => {
    resizeState.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
  }, []);

  if (!open) {
    return (
      <button
        type="button"
        onClick={toggle}
        title="AI Chat (⌘I)"
        aria-label="Open AI Chat"
        data-testid="sb-toggle-chat"
        className="fixed bottom-20 right-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg transition-all hover:bg-indigo-500 hover:scale-105 focus:outline-none focus:ring-2 focus:ring-indigo-400"
      >
        <MessageSquare size={20} />
      </button>
    );
  }

  return (
    <div
      role="dialog"
      aria-label="AI Chat"
      data-testid="sb-panel-chat"
      style={{ right, bottom, width, height }}
      className="fixed z-40 flex flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl"
    >
      {/* Title bar — drag zone (pointer-captured). The ⋮⋮ grip opens the
          session history (kept as before); the rest of the bar drags. */}
      <div
        onPointerDown={onDragDown}
        onPointerMove={onDragMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        style={{ touchAction: 'none' }}
        className="flex h-9 flex-shrink-0 cursor-grab select-none items-center gap-1.5 border-b border-ink-800 bg-ink-800/70 px-2 active:cursor-grabbing"
      >
        <button
          type="button"
          onClick={() => setSessionsOpen((v) => !v)}
          title="Chat History"
          aria-label="Toggle session history"
          className="rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-ink-200"
        >
          <GripHorizontal size={15} />
        </button>
        <span className="text-xs font-medium text-ink-300">AI Chat</span>
        {pageContext?.moduleLabel && (
          <span className="rounded bg-indigo-500/10 px-1.5 py-0.5 text-[11px] font-medium text-indigo-400">
            {pageContext.moduleLabel}
          </span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={minimize}
          title="Minimize (Esc)"
          aria-label="Minimize AI Chat"
          className="rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-ink-200"
        >
          <Minus size={14} />
        </button>
      </div>

      {/* Chat core — the shared AIChatPanel, with the live page context.
          New sessions stamp the current module; ongoing sessions are
          never interrupted by navigation (B-mode). */}
      <div className="min-h-0 flex-1">
        <AIChatPanel
          projectId={pageContext?.projectId}
          contextType={pageContext?.contextType}
          contextId={pageContext?.contextId}
          onApplyContent={pageContext?.onApplyContent}
          onClose={minimize}
          sessionsOverlayOpen={sessionsOpen}
          onSessionsOverlayClose={() => setSessionsOpen(false)}
        />
      </div>

      {/* Resize handles — top/left edges + a VISIBLE top-left corner grip.
          Window is anchored bottom-right, so it grows up/left. Pointer-
          captured; 8px edge hit areas. touchAction:none prevents scroll
          from stealing the gesture. */}
      <div
        onPointerDown={onResizeDown('n')}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        style={{ touchAction: 'none' }}
        className="absolute left-4 right-4 top-0 z-30 h-2 cursor-ns-resize"
      />
      <div
        onPointerDown={onResizeDown('w')}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        style={{ touchAction: 'none' }}
        className="absolute bottom-4 left-0 top-4 z-30 w-2 cursor-ew-resize"
      />
      <div
        onPointerDown={onResizeDown('nw')}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
        style={{ touchAction: 'none' }}
        title="Resize"
        className="absolute left-0 top-0 z-40 flex h-4 w-4 cursor-nwse-resize items-start justify-start"
      >
        <svg width="10" height="10" viewBox="0 0 10 10" className="text-ink-500">
          <path
            d="M0 3 L3 0 M0 6 L6 0 M0 9 L9 0"
            stroke="currentColor"
            strokeWidth="1"
          />
        </svg>
      </div>
    </div>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

export default FloatingChatWidget;
