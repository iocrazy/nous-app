/**
 * FloatingChatWidget — the global AI chat shell (replaces AIChatDrawer).
 *
 * Collapsed: a bottom-right FAB on every page. Expanded: a floating window
 * that can be dragged by its title bar and resized from every edge and
 * corner (anchored bottom-right: east/south drags adjust width/height AND
 * right/bottom together so the grabbed edge tracks the pointer). Rect +
 * open state persist via globalChatStore, which also lets
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
import { useLocation } from 'react-router-dom';
import { GripHorizontal, GripVertical, MessageSquare, Minus } from 'lucide-react';

import { AIChatPanel } from './AIChatPanel';
import {
  CHAT_MAX_W,
  CHAT_MIN_H,
  CHAT_MIN_W,
  useGlobalChatStore,
} from '../stores/globalChatStore';

type ResizeMode = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

export function FloatingChatWidget(): React.ReactElement | null {
  // Hide entirely on the Chat page (/team/:id/chat) — its main view IS a
  // chat (channels + agent DMs), so the floating widget would be a second
  // chat on top of a chat. State is preserved; the widget reappears (still
  // open if it was open) as soon as the user navigates away.
  const { pathname } = useLocation();
  const onChatPage = /\/chat(\/|$)/.test(pathname);

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
  const sessionsOpenRef = useRef(sessionsOpen);
  sessionsOpenRef.current = sessionsOpen;

  // Keep the window inside the viewport. The rect persists in localStorage,
  // so a size/position saved on a larger browser window can put the title
  // bar ABOVE the visible area on a smaller one — the window then looks
  // "stuck" (nothing to grab, no minimize). Re-fit whenever the widget
  // opens and whenever the browser window resizes.
  useEffect(() => {
    if (!open) return;
    const fit = () => {
      const s = useGlobalChatStore.getState();
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const w = clamp(s.width, CHAT_MIN_W, Math.max(CHAT_MIN_W, vw - 16));
      const h = clamp(s.height, CHAT_MIN_H, Math.max(CHAT_MIN_H, vh - 48));
      const r = clamp(s.right, 8, Math.max(8, vw - w - 8));
      const b = clamp(s.bottom, 8, Math.max(8, vh - h - 8));
      if (w !== s.width || h !== s.height || r !== s.right || b !== s.bottom) {
        setRect({ width: w, height: h, right: r, bottom: b });
      }
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [open, setRect]);

  // Keyboard: ⌘I / Ctrl+I toggle. ESC closes the sessions overlay first,
  // then minimizes the window.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && useGlobalChatStore.getState().open) {
        e.preventDefault();
        if (sessionsOpenRef.current) {
          setSessionsOpen(false);
          return;
        }
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

  // ── Drag (title bar) ─────────────────────────────────────────────────
  const dragState = useRef<{
    startX: number;
    startY: number;
    startRight: number;
    startBottom: number;
  } | null>(null);

  const onDragStart = useCallback(
    (e: React.PointerEvent) => {
      // Buttons inside the title bar keep their own click behavior.
      if ((e.target as HTMLElement).closest('button')) return;
      e.preventDefault();
      const s = useGlobalChatStore.getState();
      dragState.current = {
        startX: e.clientX,
        startY: e.clientY,
        startRight: s.right,
        startBottom: s.bottom,
      };
      const onMove = (ev: PointerEvent) => {
        const d = dragState.current;
        if (!d) return;
        const { width: w, height: h } = useGlobalChatStore.getState();
        const maxRight = window.innerWidth - w - 8;
        const maxBottom = window.innerHeight - h - 8;
        setRect({
          right: clamp(d.startRight - (ev.clientX - d.startX), 8, Math.max(8, maxRight)),
          bottom: clamp(d.startBottom - (ev.clientY - d.startY), 8, Math.max(8, maxBottom)),
        });
      };
      const onUp = () => {
        dragState.current = null;
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [setRect],
  );

  // ── Resize (all edges + corners; window anchored bottom-right) ───────
  // West/north drags only change width/height (the right/bottom offsets
  // stay put). East/south drags must move width AND right (height AND
  // bottom) together so the grabbed edge follows the pointer while the
  // opposite edge stays fixed on screen.
  const resizeState = useRef<{
    mode: ResizeMode;
    startX: number;
    startY: number;
    startW: number;
    startH: number;
    startRight: number;
    startBottom: number;
  } | null>(null);

  const onResizeStart = useCallback(
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
        startRight: s.right,
        startBottom: s.bottom,
      };
      const onMove = (ev: PointerEvent) => {
        const r = resizeState.current;
        if (!r) return;
        const dx = ev.clientX - r.startX;
        const dy = ev.clientY - r.startY;
        const next: Partial<{
          width: number;
          height: number;
          right: number;
          bottom: number;
        }> = {};
        const maxH = window.innerHeight - 48;
        if (r.mode.includes('w')) {
          // Left edge follows the pointer; right edge anchored. Cap width
          // so the left edge can't be pushed past the viewport's left side.
          const maxW = Math.min(CHAT_MAX_W, window.innerWidth - r.startRight - 8);
          next.width = clamp(r.startW - dx, CHAT_MIN_W, Math.max(CHAT_MIN_W, maxW));
        }
        if (r.mode.includes('e')) {
          // Right edge follows the pointer; LEFT edge anchored — width and
          // right offset change in lockstep. Cap so right stays ≥ 8px.
          const maxW = Math.min(CHAT_MAX_W, r.startW + r.startRight - 8);
          const w = clamp(r.startW + dx, CHAT_MIN_W, Math.max(CHAT_MIN_W, maxW));
          next.width = w;
          next.right = r.startRight - (w - r.startW);
        }
        if (r.mode.includes('n')) {
          next.height = clamp(r.startH - dy, CHAT_MIN_H, maxH);
        }
        if (r.mode.includes('s')) {
          // Bottom edge follows the pointer; TOP edge anchored.
          const cap = Math.min(maxH, r.startH + r.startBottom - 8);
          const h = clamp(r.startH + dy, CHAT_MIN_H, Math.max(CHAT_MIN_H, cap));
          next.height = h;
          next.bottom = r.startBottom - (h - r.startH);
        }
        setRect(next);
      };
      const onUp = () => {
        resizeState.current = null;
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    },
    [setRect],
  );

  if (onChatPage) return null;

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
      {/* Title bar — drag handle + module chip + minimize */}
      <div
        onPointerDown={onDragStart}
        className="flex h-10 flex-shrink-0 cursor-grab select-none items-center gap-2 border-b border-ink-800 bg-ink-900/95 px-2 active:cursor-grabbing"
      >
        {/* Grab affordance. A <span>, not a <button> — onDragStart skips
            anything inside a button, so a button here would never drag. */}
        <span
          data-testid="chat-drag-handle"
          aria-hidden="true"
          className="flex cursor-grab items-center text-ink-500 active:cursor-grabbing"
        >
          <GripVertical size={14} />
        </span>
        <button
          type="button"
          onClick={() => setSessionsOpen((v) => !v)}
          title="Chat History"
          aria-label="Toggle session history"
          className="rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-200"
        >
          <GripHorizontal size={14} />
        </button>
        {pageContext?.moduleLabel && (
          <span className="rounded bg-[var(--accent-soft)] px-1.5 py-0.5 text-[11px] font-medium text-[var(--accent-text)]">
            {pageContext.moduleLabel}
          </span>
        )}
        <div className="flex-1" />
        <button
          type="button"
          onClick={minimize}
          title="Minimize (Esc)"
          aria-label="Minimize AI Chat"
          className="rounded p-1 text-ink-400 hover:bg-ink-800 hover:text-ink-200"
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

      {/* Resize handles — all four edges + four corners */}
      <div
        onPointerDown={onResizeStart('n')}
        className="absolute left-3 right-3 top-0 h-1.5 cursor-ns-resize"
      />
      <div
        onPointerDown={onResizeStart('s')}
        className="absolute bottom-0 left-3 right-3 h-1.5 cursor-ns-resize"
      />
      <div
        onPointerDown={onResizeStart('w')}
        className="absolute bottom-3 left-0 top-3 w-1.5 cursor-ew-resize"
      />
      <div
        onPointerDown={onResizeStart('e')}
        className="absolute bottom-3 right-0 top-3 w-1.5 cursor-ew-resize"
      />
      <div
        onPointerDown={onResizeStart('nw')}
        className="absolute left-0 top-0 h-3 w-3 cursor-nwse-resize"
      />
      <div
        onPointerDown={onResizeStart('ne')}
        className="absolute right-0 top-0 h-3 w-3 cursor-nesw-resize"
      />
      <div
        onPointerDown={onResizeStart('sw')}
        className="absolute bottom-0 left-0 h-3 w-3 cursor-nesw-resize"
      />
      <div
        onPointerDown={onResizeStart('se')}
        className="absolute bottom-0 right-0 h-3 w-3 cursor-nwse-resize"
      />
    </div>
  );
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

export default FloatingChatWidget;
