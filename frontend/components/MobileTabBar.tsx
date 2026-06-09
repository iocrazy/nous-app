import React, { useLayoutEffect, useRef, useState } from 'react';
import { useTaskManager } from '../contexts/TaskManagerContext';

// ---------------------------------------------------------------------------
// MobileTabBar — the mobile bottom navigation.
//
// Two visual states cross-fade based on `collapsed` (driven by scroll position
// via useTabBarCollapse, pixcall-style):
//   • EXPANDED — the full floating pill, centered, with a sliding active
//     indicator that morphs between tabs on switch (iOS-26-ish).
//   • COLLAPSED — a single circular button at the bottom-left showing only the
//     active tab's icon; tapping it re-expands.
//
// Cross-fade (not single-element morph) is deliberate: there's no
// framer-motion, and measuring tab geometry mid-collapse-transition is
// unreliable. Two elements with opacity/scale transitions are robust and read
// the same as pixcall's bar↔dot.
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
  const activeTab = tabs[activeIndex];
  const activeBadge = activeTab?.showActiveBadge ? totalActive : 0;

  const containerRef = useRef<HTMLDivElement>(null);
  const cellRefs = useRef<(HTMLElement | null)[]>([]);
  const [indicator, setIndicator] = useState<{ left: number; width: number }>({ left: 0, width: 0 });

  // Position the sliding indicator under the active tab. Measured via
  // bounding rects (offsetLeft is unreliable — popup tabs wrap the button in a
  // relative div, changing the offsetParent chain). Recompute on active change,
  // tab-count change, collapse toggle, and resize.
  useLayoutEffect(() => {
    const measure = () => {
      const c = containerRef.current;
      const cell = cellRefs.current[activeIndex];
      if (!c || !cell) return;
      const cr = c.getBoundingClientRect();
      const br = cell.getBoundingClientRect();
      setIndicator({ left: br.left - cr.left, width: br.width });
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [activeIndex, tabs.length, collapsed]);

  const renderBadge = (count: number) =>
    count > 0 ? (
      <span className="absolute top-0 right-2 flex items-center justify-center min-w-[16px] h-[16px] px-1 text-[9px] font-bold leading-none text-white bg-indigo-500 rounded-full">
        {count > 99 ? '99+' : count}
      </span>
    ) : null;

  return (
    <div className={`sm:hidden fixed bottom-0 left-0 right-0 z-[45] ${hidden ? 'hidden' : ''}`}>
      <div className="relative pb-[calc(env(safe-area-inset-bottom,6px)+6px)]">
        {/* Collapsed state — single circular button, bottom-left */}
        <button
          onClick={onExpand}
          aria-label="Expand navigation"
          className={`absolute left-4 bottom-[calc(env(safe-area-inset-bottom,6px)+6px)] w-12 h-12 rounded-full bg-zinc-900/95 backdrop-blur-xl border border-zinc-800/60 shadow-2xl flex items-center justify-center transition-all duration-300 ease-out ${
            hasActive ? 'text-indigo-400' : 'text-zinc-400'
          } ${collapsed ? 'opacity-100 scale-100 pointer-events-auto' : 'opacity-0 scale-75 pointer-events-none'}`}
        >
          {activeTab?.icon}
          {renderBadge(activeBadge)}
        </button>

        {/* Expanded state — full floating pill */}
        <div
          className={`flex justify-center transition-all duration-300 ease-out ${
            collapsed ? 'opacity-0 translate-y-2 pointer-events-none' : 'opacity-100 translate-y-0'
          }`}
        >
          <div
            ref={containerRef}
            className="relative bg-zinc-900/95 backdrop-blur-xl border border-zinc-800/60 rounded-full flex items-center px-2 py-1.5 gap-0.5 shadow-2xl"
          >
            {/* Sliding active indicator */}
            <div
              className={`absolute top-1.5 bottom-1.5 rounded-full bg-zinc-800 transition-[transform,width,opacity] duration-300 ease-out ${
                hasActive ? 'opacity-100' : 'opacity-0'
              }`}
              style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }}
            />

            {tabs.map((tab, i) => (
              <div
                key={tab.key}
                ref={(el) => { cellRefs.current[i] = el; }}
                className="relative"
              >
                {tab.popup}
                <button
                  onClick={tab.onClick}
                  className={`relative z-10 flex flex-col items-center gap-0.5 px-4 py-1.5 rounded-full transition-colors ${
                    tab.active ? 'text-indigo-400' : 'text-zinc-500'
                  }`}
                >
                  {tab.icon}
                  <span className="text-[9px] leading-tight font-medium">{tab.label}</span>
                  {tab.showActiveBadge ? renderBadge(totalActive) : null}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
