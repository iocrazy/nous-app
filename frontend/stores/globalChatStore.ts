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

/** One-shot "open the panel and talk to THIS agent" request (AI Library
 *  sidebar's Chat section). Nonce disambiguates repeat clicks on the same
 *  agent. Consumed (cleared) by AIChatPanel once applied; never persisted. */
export interface ChatRequest {
  agentSlug: string;
  /** Optional: also resume THIS session (Sessions page "Open in Chat"). */
  sessionId?: string;
  nonce: number;
}

/** One-shot "send this selected text into the chat as a quoted reference"
 *  request (Script editor's selection → AI chat pill). The selected text is
 *  injected into the composer as a blockquote, tagged with its source scene,
 *  and the input is focused so the user can immediately give an instruction.
 *  Nonce disambiguates repeat sends of the same text. Consumed (cleared) by
 *  AIChatPanel once injected; never persisted. */
export interface PendingQuote {
  /** The selected plain text. */
  text: string;
  /** Short label for the source scene, e.g. "S2" or the heading. */
  sceneLabel?: string;
  /** Source scene id (structural context). */
  sceneId?: string;
  /** Source element id + type (structural context). */
  elementId?: string;
  elementType?: string;
  /** True when the selection spanned more than one scene. */
  crossScene?: boolean;
  nonce: number;
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
  /** Pending open-with-agent request (not persisted). */
  chatRequest: ChatRequest | null;
  /** Pending "quote this selection into the composer" request (not persisted). */
  pendingQuote: PendingQuote | null;

  setOpen: (open: boolean) => void;
  toggle: () => void;
  setRect: (
    rect: Partial<Pick<GlobalChatState, 'right' | 'bottom' | 'width' | 'height'>>,
  ) => void;
  setPageContext: (ctx: PageChatContext | null) => void;
  /** Open the floating chat targeted at an agent (Chat section entry);
   *  pass sessionId to also resume a specific session (Sessions page). */
  requestChat: (agentSlug: string, sessionId?: string) => void;
  consumeChatRequest: () => void;
  /** Open the floating chat and stage a quoted selection for the composer. */
  sendSelectionToChat: (quote: Omit<PendingQuote, 'nonce'>) => void;
  consumePendingQuote: () => void;
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
      chatRequest: null,
      pendingQuote: null,

      setOpen: (open) => set({ open }),
      toggle: () => set((s) => ({ open: !s.open })),
      setRect: (rect) => set(rect),
      setPageContext: (ctx) => set({ pageContext: ctx }),
      requestChat: (agentSlug, sessionId) =>
        set((s) => ({
          open: true,
          chatRequest: { agentSlug, sessionId, nonce: (s.chatRequest?.nonce ?? 0) + 1 },
        })),
      consumeChatRequest: () => set({ chatRequest: null }),
      sendSelectionToChat: (quote) =>
        set((s) => ({
          open: true,
          pendingQuote: { ...quote, nonce: (s.pendingQuote?.nonce ?? 0) + 1 },
        })),
      consumePendingQuote: () => set({ pendingQuote: null }),
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
