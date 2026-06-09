import React, { useLayoutEffect, useRef, useState } from 'react';
import { useTaskManager } from '../contexts/TaskManagerContext';

// ---------------------------------------------------------------------------
// MobileTabBar — the mobile bottom navigation.
//
// One pill that MORPHS between two states (driven by `collapsed`, pixcall-style):
//   • EXPANDED — full floating pill, centered, with a sliding active indicator
//     that animates between tabs on switch.
//   • COLLAPSED — slides to the bottom-left and shrinks to just the active tab's
//     icon (non-active tabs collapse their width to 0). Tapping it re-expands.
//
// Single-element morph (not a cross-fade of two elements) so the collapse reads
// as one continuous motion on the same row, not a jump between positions.
// ---------------------------------------------------------------------------

export interface MobileTab {
  key: string;
  icon: React.ReactNode;
  label: string;
  active: boolean;
  onClick: () => void;
  /** Tasks tab: render a live active-task count badge (reads TaskManager). */
  showActiveBadge?: boolean;
  /** Popup anchored above this tab (Resources sidebar / Downloads view-mode). */
  popup?: React.ReactNode;
}

interface MobileTabBarProps {
  tabs: MobileTab[];
  collapsed: boolean;
  onExpand: () => void;
  hidden?: boolean;
}

export function MobileTabBar({ tabs, collapsed, onExpand, hidden }: MobileTabBarProps) {
  const { totalActive } = useTaskManager();

  const hasActive = tabs.some((t) => t.active);
  const activeIndex = Math.max(0, tabs.findIndex((t) => t.active));

  const containerRef = useRef<HTMLDivElement>(null);
  const cellRefs = useRef<(HTMLElement | null)[]>([]);
  const [indicator, setIndicator] = useState<{ left: number; width: number }>({ left: 0, width: 0 });

  // Position the sliding indicator under the active tab (expanded state only).
  // Measured via bounding rects — offsetLeft is unreliable since popup tabs
  // wrap the button in a relative div.
  //
  // A ResizeObserver on the container + a document.fonts.ready hook are the
  // load-bearing bits: the web font (Inter) loads AFTER first paint (FOUT), which
  // widens the tab labels and shifts every cell. Without re-measuring on that
  // reflow the indicator stays at the fallback-font positions and drifts left.
  // The observer also covers the collapse/expand morph and any width change.
  useLayoutEffect(() => {
    const c = containerRef.current;
    const measure = () => {
      const cell = cellRefs.current[activeIndex];
      if (!c || !cell) return;
      const cr = c.getBoundingClientRect();
      const br = cell.getBoundingClientRect();
      // Offset from the container's PADDING box (the indicator is anchored at
      // left-0 = padding-box origin). Subtract clientLeft (the left border
      // width) so translateX lands exactly on the cell.
      setIndicator({ left: br.left - cr.left - c.clientLeft, width: br.width });
    };
    measure();
    const settle = window.setTimeout(measure, 320);
    window.addEventListener('resize', measure);
    let ro: ResizeObserver | undefined;
    if (c && typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => measure());
      ro.observe(c);
    }
    // Re-measure once web fonts finish loading (Inter widens the labels).
    const fonts = (document as Document & { fonts?: FontFaceSet }).fonts;
    if (fonts?.ready) fonts.ready.then(measure).catch(() => {});
    return () => {
      window.clearTimeout(settle);
      window.removeEventListener('resize', measure);
      ro?.disconnect();
    };
  }, [activeIndex, tabs.length, collapsed]);

  const renderBadge = (count: number) =>
    count > 0 ? (
      <span className="absolute top-0 right-1.5 flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[9px] font-bold leading-none text-white bg-indigo-500 rounded-full">
        {count > 99 ? '99+' : count}
      </span>
    ) : null;

  return (
    <div className={`sm:hidden fixed inset-x-0 bottom-0 z-[49] pointer-events-none ${hidden ? 'hidden' : ''}`}>
      <div
        className={`pointer-events-auto absolute bottom-[calc(env(safe-area-inset-bottom,6px)+6px)] transition-[left,transform] duration-300 ease-out ${
          collapsed ? 'left-4 translate-x-0' : 'left-1/2 -translate-x-1/2'
        }`}
      >
        <div
          ref={containerRef}
          className="relative bg-zinc-900/95 backdrop-blur-xl border border-zinc-800/60 rounded-full flex items-center px-2 py-1.5 gap-0.5 shadow-2xl"
        >
          {/* Sliding active indicator — iOS-26 "liquid glass": translucent fill
              + top highlight + soft shadow, with a springy (overshoot) ease so
              the switch reads as a glass pill snapping into place. Hidden while
              collapsed (the dot is the container itself). */}
          <div
            className={`absolute left-0 top-1.5 bottom-1.5 rounded-full bg-white/10 border border-white/15 backdrop-blur-md shadow-[0_2px_10px_rgba(0,0,0,0.35),inset_0_1px_0_rgba(255,255,255,0.22)] transition-[transform,width,opacity] duration-[420ms] ease-[cubic-bezier(0.34,1.56,0.64,1)] ${
              hasActive && !collapsed ? 'opacity-100' : 'opacity-0'
            }`}
            style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }}
          />

          {tabs.map((tab, i) => {
            const hideCell = collapsed && !tab.active;
            return (
              <div
                key={tab.key}
                ref={(el) => { cellRefs.current[i] = el; }}
                className={`relative overflow-visible transition-[max-width,opacity] duration-300 ease-out ${
                  hideCell ? 'max-w-0 opacity-0 pointer-events-none' : 'max-w-[120px] opacity-100'
                }`}
              >
                {tab.popup}
                <button
                  onClick={collapsed ? onExpand : tab.onClick}
                  className={`relative z-10 flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
                    tab.active ? 'text-indigo-400' : 'text-zinc-500'
                  }`}
                >
                  {tab.icon}
                  {!collapsed && (
                    <span className="text-[9px] leading-tight font-medium whitespace-nowrap">{tab.label}</span>
                  )}
                  {tab.showActiveBadge ? renderBadge(totalActive) : null}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
