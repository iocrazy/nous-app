// features/canvas-core/library/LibraryPanel.tsx
//
// The island panel — ONE Library on the canvas, where an Asset chip, a
// Project Assets chip and an Add-reference popover used to be three.
//
// It floats over the canvas rather than squeezing it: the board under it keeps
// its size and keeps panning, which is what makes "drag a picture from the
// panel onto a node" a single gesture (Task 8).
//
// This file is the island's CHROME — geometry, key trapping, which page is
// showing, and which node the panel is aiming at. The media surface's own
// arithmetic (quota, scopes, kind chips, the two commit paths) lives in
// `LibraryMediaPage`; the target is resolved HERE and handed down, so exactly
// one place decides whether an aim is still live.
//
// ⚠️ `useCanvasShortcuts` decides "am I typing?" from the EVENT TARGET
// (INPUT / TEXTAREA / SELECT / contentEditable). The search box is covered by
// that; the panel's own chrome is not — so `L` pressed with the panel focused
// but no field active would reach the canvas and toggle the panel shut under
// the user's hand. The root therefore stops keydown propagation, and lets
// Escape through on purpose: Escape means "get me out of here" at every level.

import React, { useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

import { useOptionalToast } from '../../../components/Toast';
import { useCanvasReadOnly } from '../smart/nodes/useCanvasReadOnly';
import type { PromptNodeData } from '../smart/types';
import { useCanvasCoreStore } from '../store/canvasCoreStore';
import { chipClass, type Label } from './libraryChrome';
import { DRAWER_HEIGHT, PANEL_GUTTER, useLibraryDrawer } from './libraryInset';
import { LibraryMediaPage } from './LibraryMediaPage';
import { LibraryPromptsPage } from './LibraryPromptsPage';
import { useLibraryStore, type LibraryPage } from './libraryStore';
import { isMentionTarget } from './libraryTarget';

const PAGE_LABEL: Record<LibraryPage, Label> = {
  media: ['canvas.library.pageMedia', 'Media'],
  prompts: ['canvas.library.pagePrompts', 'Prompts'],
};

export function LibraryPanel(): React.ReactElement | null {
  const { t } = useTranslation();
  const toast = useOptionalToast();
  const nodes = useCanvasCoreStore((s) => s.nodes);
  // The panel mounts for a viewer too — `L` is view-only. What a viewer must
  // not get is the target bar: nothing in a read-only session can arm a
  // target, and a bar promising to add references would be a promise the
  // media page refuses to keep.
  const readOnly = useCanvasReadOnly();

  const open = useLibraryStore((s) => s.open);
  const page = useLibraryStore((s) => s.page);
  const target = useLibraryStore((s) => s.target);
  const width = useLibraryStore((s) => s.width);
  const focusNonce = useLibraryStore((s) => s.focusNonce);

  const rootRef = useRef<HTMLDivElement>(null);
  // Shared with `libraryInset`, which reserves surface for whichever
  // layout this returns. Two copies of the breakpoint would let the panel
  // draw a bottom drawer while the islands still dodged a right dock —
  // the original bug wearing a different hat.
  const drawer = useLibraryDrawer();

  // ── The target node, re-read from the store every render ────────────────
  // The id is the authority; `target.title` is only what the node was CALLED
  // when the panel opened. Reading the node back is also how the quota stays
  // live while references land on it.
  const targetData = useMemo(() => {
    if (!target) return null;
    const node = nodes.find((n) => (n as { id?: unknown }).id === target.nodeId) as
      | { data?: PromptNodeData }
      | undefined;
    return node ? ((node.data ?? {}) as PromptNodeData) : null;
  }, [nodes, target]);

  // A target whose node has been deleted must LET GO, out loud. Aiming at a
  // node that is no longer there would make the next Add land nowhere, and
  // nothing else on screen would say why.
  useEffect(() => {
    if (!target || targetData !== null) return;
    useLibraryStore.getState().clearTarget();
    toast?.addToast(t('canvas.library.targetGone', 'Target node was removed'), 'info');
  }, [target, targetData, toast, t]);

  // A NONCE, not a ref handed down: the search input belongs to LibraryGrid,
  // and threading a ref through it for one caller would put focus plumbing in
  // a component that has no other reason to know about focus.
  useEffect(() => {
    if (!useLibraryStore.getState().open) return;
    rootRef.current?.querySelector<HTMLInputElement>('[data-testid="library-search"]')?.focus();
  }, [focusNonce]);

  if (!open) return null;

  const inTargetMode =
    !readOnly && target !== null && target.kind === 'prompt' && targetData !== null;
  // Same predicate `LibraryMediaPage` picks its primary action from, imported
  // rather than re-derived: a bar that says "Adding references" over a page
  // whose button inserts chips would be the lie the user reads first.
  const mentionMode = inTargetMode && isMentionTarget(targetData);

  return (
    <div
      ref={rootRef}
      data-testid="library-panel"
      role="dialog"
      aria-label={t('canvas.library.title', 'Library')}
      className="canvas-island nodrag nowheel nopan pointer-events-auto absolute z-30 flex flex-col overflow-hidden"
      style={
        drawer
          ? {
              left: PANEL_GUTTER,
              right: PANEL_GUTTER,
              bottom: PANEL_GUTTER,
              height: DRAWER_HEIGHT,
            }
          : { top: PANEL_GUTTER, right: PANEL_GUTTER, bottom: PANEL_GUTTER, width }
      }
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          useLibraryStore.getState().close();
          return;
        }
        // Everything else stops here. Escape is deliberately let through: it
        // means "get me out of here" at every level of the canvas.
        e.stopPropagation();
      }}
    >
      <div className="flex items-center gap-1 border-b border-canvas-line px-2 py-1.5">
        {(Object.keys(PAGE_LABEL) as LibraryPage[]).map((p) => (
          <button
            key={p}
            type="button"
            data-testid={`library-page-${p}`}
            aria-pressed={page === p}
            onClick={() => useLibraryStore.getState().setPage(p)}
            className={chipClass(page === p)}
          >
            {t(PAGE_LABEL[p][0], PAGE_LABEL[p][1])}
          </button>
        ))}
        <span className="flex-1" />
        <button
          type="button"
          data-testid="library-close"
          aria-label={t('canvas.library.close', 'Close')}
          onClick={() => useLibraryStore.getState().close()}
          className="nodrag flex h-5 w-5 items-center justify-center rounded text-canvas-muted hover:text-canvas-text"
        >
          <X size={13} />
        </button>
      </div>

      {inTargetMode && (
        <div
          data-testid="library-target"
          className="flex items-center gap-1 bg-ok-soft px-2 py-1 text-[11px] text-ok"
        >
          <span className="min-w-0 flex-1 truncate">
            {mentionMode
              ? t('canvas.library.targetPromptMention', {
                  title: target.title,
                  defaultValue: 'Inserting mentions into {{title}}',
                })
              : t('canvas.library.targetPrompt', {
                  title: target.title,
                  defaultValue: 'Adding references to {{title}}',
                })}
          </span>
          <button
            type="button"
            data-testid="library-target-clear"
            aria-label={t('canvas.library.clearTarget', 'Stop Aiming')}
            onClick={() => useLibraryStore.getState().clearTarget()}
            className="nodrag flex h-4 w-4 shrink-0 items-center justify-center rounded"
          >
            <X size={11} />
          </button>
        </div>
      )}

      {page === 'prompts' ? (
        <LibraryPromptsPage target={target} targetData={targetData} />
      ) : (
        <LibraryMediaPage target={target} targetData={targetData} />
      )}
    </div>
  );
}

export default LibraryPanel;
