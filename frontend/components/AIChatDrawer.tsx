/**
 * AIChatDrawer — right-side slide-in drawer that wraps AIChatPanel.
 *
 * Single chat entry point shared by ScriptEditor + StoryboardWorkbench.
 * A floating action button at the bottom-right toggles the drawer.
 *
 *   - Width: 400px fixed (designed for sub-task cards + message bubbles)
 *   - Animation: 200ms ease-out translateX
 *   - Open state persisted in localStorage; first visit defaults closed
 *     so the canvas isn't immediately covered.
 *   - Keyboard: ⌘I / Ctrl+I to toggle, ESC to close.
 *
 * Layered above the canvas (z-30); does not push main content.
 */

import React, { useCallback, useEffect, useState } from 'react';
import { MessageSquare } from 'lucide-react';

import { AIChatPanel } from './AIChatPanel';

const STORAGE_KEY = 'mediahub.ai_chat_drawer_open';
const DRAWER_WIDTH = 400; // px

export interface AIChatDrawerProps {
  /** Required so AIChatPanel can scope sessions to this project. */
  projectId: string;
  contextType?: 'script' | 'storyboard';
  contextId?: string;
  /** Called when the user hits "Apply" on an assistant bubble. */
  onApplyContent?: (content: string) => void;
}

function readPersistedOpen(): boolean {
  // Server-render safe: window may not exist in SSR contexts.
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function persistOpen(open: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, open ? 'true' : 'false');
  } catch {
    // Swallow quota / privacy-mode errors — not worth surfacing.
  }
}

export function AIChatDrawer({
  projectId,
  contextType,
  contextId,
  onApplyContent,
}: AIChatDrawerProps): React.ReactElement {
  const [open, setOpen] = useState<boolean>(readPersistedOpen);

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((v) => !v), []);

  useEffect(() => {
    persistOpen(open);
  }, [open]);

  // Keyboard: ESC to close, ⌘I / Ctrl+I to toggle.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && open) {
        e.preventDefault();
        close();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'i') {
        e.preventDefault();
        toggle();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, close, toggle]);

  return (
    <>
      {/* Floating action button — hidden when drawer is open so the
          drawer's own close button is the only affordance. */}
      {!open && (
        <button
          type="button"
          onClick={toggle}
          title="AI Chat (⌘I)"
          aria-label="Open AI Chat"
          data-testid="sb-toggle-chat"
          className="fixed bottom-4 right-4 z-30 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg transition-all hover:bg-indigo-500 hover:scale-105 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        >
          <MessageSquare size={20} />
        </button>
      )}

      {/* Drawer container — always rendered so the slide animation runs
          on every open/close. translate-x-full hides it off-screen. */}
      <aside
        aria-hidden={!open}
        aria-label="AI Chat panel"
        style={{ width: DRAWER_WIDTH }}
        className={`fixed top-0 right-0 z-30 h-screen border-l border-ink-800 bg-ink-900 shadow-2xl transition-transform duration-200 ease-out ${
          open ? 'translate-x-0' : 'translate-x-full pointer-events-none'
        }`}
      >
        {open && (
          <div data-testid="sb-panel-chat" className="h-full">
            <AIChatPanel
              projectId={projectId}
              contextType={contextType}
              contextId={contextId}
              onApplyContent={onApplyContent}
              onClose={close}
            />
          </div>
        )}
      </aside>
    </>
  );
}

export default AIChatDrawer;
