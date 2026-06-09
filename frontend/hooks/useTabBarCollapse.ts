import { useCallback, useEffect, useState } from 'react';

// px tolerance for treating a scroll surface as sitting at its top / bottom edge.
const EDGE = 8;

interface ScrollMetrics {
  scrollTop: number;
  viewport: number;
  full: number;
}

function readWindow(): ScrollMetrics {
  return {
    scrollTop: window.scrollY,
    viewport: window.innerHeight,
    full: document.documentElement.scrollHeight,
  };
}

function readEl(el: HTMLElement): ScrollMetrics {
  return { scrollTop: el.scrollTop, viewport: el.clientHeight, full: el.scrollHeight };
}

function atEdge({ scrollTop, viewport, full }: ScrollMetrics): boolean {
  // Content that doesn't overflow the viewport has nothing to browse → stay open.
  if (full <= viewport + EDGE) return true;
  const atTop = scrollTop <= EDGE;
  const atBottom = scrollTop + viewport >= full - EDGE;
  return atTop || atBottom;
}

/**
 * Pixcall-style collapsible bottom tab bar.
 *
 * The bar is EXPANDED while the active scroll surface sits at its top or
 * bottom edge, and COLLAPSES once the user scrolls into the middle of the
 * content (so it stays out of the way while browsing). Tapping the collapsed
 * bar forces it back open until the next mid-scroll.
 *
 * Main tab pages scroll the window (natural mobile scroll) — handled by the
 * built-in window listener. Full-screen overlays (e.g. the Tasks page) scroll
 * an inner element instead; they call ``handleScroll(el)`` from that element's
 * onScroll so the same collapse logic drives one shared bar.
 */
export function useTabBarCollapse() {
  const [collapsed, setCollapsed] = useState(false);

  const handleScroll = useCallback((el?: HTMLElement | null) => {
    setCollapsed(!atEdge(el ? readEl(el) : readWindow()));
  }, []);

  useEffect(() => {
    const onScroll = () => handleScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    handleScroll(); // initial state on mount
    return () => window.removeEventListener('scroll', onScroll);
  }, [handleScroll]);

  // Force open (tap on the collapsed pill).
  const expand = useCallback(() => setCollapsed(false), []);

  // Re-evaluate against the window — call on route / overlay changes so the bar
  // reflects the newly visible page's scroll position.
  const resetToWindow = useCallback(() => handleScroll(), [handleScroll]);

  return { collapsed, expand, handleScroll, resetToWindow };
}
