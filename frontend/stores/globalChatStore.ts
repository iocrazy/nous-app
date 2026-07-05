/**
 * globalChatStore — window state for the global floating AI chat.
 *
 * Module-level (zustand) rather than a React provider on purpose: the chat
 * widget is mounted in TWO hosts — AppLayout (all normal routes) and the
 * fullscreen editor routes (Script / Storyboard live OUTSIDE AppLayout) —
 * and the open/position/size/session state must survive navigation between
 * them. Only one host renders the widget at a time.
 *
 * Route awareness is "sense, don't interrupt" (B-mode): pages register a
 * PageChatContext on mount; the widget shows it as a chip and NEW sessions
 * pick it up automatically, but an ongoing session is never switched away.
 */

import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export interface PageChatContext {
  projectId?: string;
  contextType?: 'script' | 'storyboard';
  contextId?: string;
  /** Short display label for the title-bar chip (e.g. "Script"). */
  moduleLabel?: string;
  /** Page-provided "apply this AI output" handler (Script editor). */
  onApplyContent?: (content: string) => void;
}

interface GlobalChatState {
  open: boolean;
  /** Window rect, anchored bottom-right (offsets in px). */
  right: number;
  bottom: number;
  width: number;
  height: number;
  /** Live context registered by the current page (not persisted). */
  pageContext: PageChatContext | null;

  setOpen: (open: boolean) => void;
  toggle: () => void;
  setRect: (
    rect: Partial<Pick<GlobalChatState, 'right' | 'bottom' | 'width' | 'height'>>,
  ) => void;
  setPageContext: (ctx: PageChatContext | null) => void;
}

export const CHAT_MIN_W = 340;
export const CHAT_MAX_W = 760;
export const CHAT_MIN_H = 420;

export const useGlobalChatStore = create<GlobalChatState>()(
  persist(
    (set) => ({
      open: false,
      right: 16,
      bottom: 16,
      width: 400,
      height: 620,
      pageContext: null,

      setOpen: (open) => set({ open }),
      toggle: () => set((s) => ({ open: !s.open })),
      setRect: (rect) => set(rect),
      setPageContext: (ctx) => set({ pageContext: ctx }),
    }),
    {
      name: 'mediahub.global_chat',
      // pageContext holds page callbacks — never persist it.
      partialize: (s) => ({
        open: s.open,
        right: s.right,
        bottom: s.bottom,
        width: s.width,
        height: s.height,
      }),
    },
  ),
);
