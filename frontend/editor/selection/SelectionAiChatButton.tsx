/**
 * SelectionAiChatButton — laper-style "select text → AI chat" pill.
 *
 * Watches text selection inside the script paper (`.mh-sheet`). Whenever a
 * non-empty run of script text is selected, a small ink pill floats centered
 * just above the selection; clicking it lifts the selected text into the global
 * AI chat as a quoted reference (tagged with its source scene) and focuses the
 * composer so the user can immediately instruct the AI about that text.
 *
 * Design guards:
 *  - `onMouseDown` prevents default so pressing the pill never steals the DOM
 *    selection or focus (the classic selection-toolbar footgun).
 *  - The `selectionchange` handler is debounced (~150ms) so dragging out a
 *    selection doesn't flicker the pill, and it bails when the resolved quote is
 *    unchanged — no state churn, no re-render thrash (cf. #1389).
 *  - Positions come straight from `Range.getBoundingClientRect()` in viewport
 *    coordinates; the pill is `position: fixed`, so under the sheet's CSS
 *    `zoom` both are in the same visual coordinate space (cf. #1429).
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Sparkles } from 'lucide-react';

import { useGlobalChatStore } from '../../stores/globalChatStore';
import {
  resolveSelectionQuote,
  type ResolveSelectionOptions,
  type SelectionQuoteContext,
} from './selectionContext';

const DEBOUNCE_MS = 150;
/** Vertical gap between the selection's top edge and the pill. */
const GAP_PX = 8;

interface FloatState {
  quote: SelectionQuoteContext;
  x: number;
  y: number;
}

/** Stable signature so an unchanged selection never re-renders the pill. */
function signature(s: FloatState | null): string {
  if (!s) return '';
  return `${s.quote.text}|${s.quote.elementId ?? ''}|${Math.round(s.x)}|${Math.round(s.y)}`;
}

export interface SelectionAiChatButtonProps {
  /** Overrides for the DOM selectors (tests / alternate hosts). */
  resolveOptions?: ResolveSelectionOptions;
}

export function SelectionAiChatButton({
  resolveOptions,
}: SelectionAiChatButtonProps): React.ReactElement | null {
  const { t } = useTranslation();
  const sendSelectionToChat = useGlobalChatStore((s) => s.sendSelectionToChat);

  const [floating, setFloating] = useState<FloatState | null>(null);
  const floatingSigRef = useRef('');
  floatingSigRef.current = signature(floating);
  const debounceRef = useRef<number | null>(null);

  const clear = useCallback(() => {
    if (floatingSigRef.current === '') return; // already hidden — bail (no churn)
    setFloating(null);
  }, []);

  const recompute = useCallback(() => {
    const sel = window.getSelection();
    const quote = resolveSelectionQuote(sel, resolveOptions);
    if (!quote || !sel || sel.rangeCount === 0) {
      clear();
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    // Degenerate rect (0×0) — nothing to anchor to; treat as no selection.
    if (rect.width === 0 && rect.height === 0) {
      clear();
      return;
    }
    const next: FloatState = {
      quote,
      x: rect.left + rect.width / 2,
      y: Math.max(GAP_PX + 4, rect.top - GAP_PX),
    };
    if (signature(next) === floatingSigRef.current) return; // unchanged — bail
    setFloating(next);
  }, [clear, resolveOptions]);

  // Debounced selectionchange — settle only after the drag pauses.
  useEffect(() => {
    const onSelectionChange = () => {
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
      debounceRef.current = window.setTimeout(recompute, DEBOUNCE_MS);
    };
    document.addEventListener('selectionchange', onSelectionChange);
    return () => {
      document.removeEventListener('selectionchange', onSelectionChange);
      if (debounceRef.current != null) window.clearTimeout(debounceRef.current);
    };
  }, [recompute]);

  // A scrolled or resized viewport makes the anchored position stale; the
  // cheapest correct behavior is to dismiss (the selection itself survives).
  // Esc dismisses too. Capture-phase scroll catches inner scrollers.
  useEffect(() => {
    const onScroll = () => clear();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') clear();
    };
    document.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onScroll);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onScroll);
      document.removeEventListener('keydown', onKey);
    };
  }, [clear]);

  const handleClick = useCallback(() => {
    if (!floating) return;
    sendSelectionToChat({
      text: floating.quote.text,
      sceneLabel: floating.quote.sceneLabel,
      sceneId: floating.quote.sceneId,
      elementId: floating.quote.elementId,
      elementType: floating.quote.elementType,
      crossScene: floating.quote.crossScene,
    });
    clear();
  }, [floating, sendSelectionToChat, clear]);

  if (!floating) return null;

  return (
    <button
      type="button"
      data-testid="selection-ai-chat"
      // Never let the pointer press steal the DOM selection / focus.
      onMouseDown={(e) => e.preventDefault()}
      onClick={handleClick}
      title={t('editor.selectionAiChatTooltip', 'Send selection to AI chat')}
      style={{
        position: 'fixed',
        left: floating.x,
        top: floating.y,
        transform: 'translate(-50%, -100%)',
        zIndex: 50,
      }}
      className="flex items-center gap-1.5 rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-xs font-medium text-ink-50 shadow-lg transition-colors hover:bg-ink-800"
    >
      <Sparkles size={13} className="text-ink-100" aria-hidden="true" />
      {t('editor.selectionAiChat', 'AI chat')}
    </button>
  );
}

export default SelectionAiChatButton;
