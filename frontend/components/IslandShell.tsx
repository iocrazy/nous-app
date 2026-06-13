import React from 'react';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';
import { IslandWorkProvider, useIslandWork } from '../contexts/IslandWorkContext';

// Island redesign v2 P1b — desktop app shell. Background micro-glow + a
// full-width global topbar strip, then a content row with a floating nav
// island and a workspace island. The heavy children (TopBar/Sidebar) keep all
// their logic; we only place them inside island cards and pass `island`. Detail
// routes pin the 54px icon rail (spec D5). Mobile chrome is rendered by
// AppLayout outside this shell and is unaffected (this frame is `hidden sm:flex`).
//
// v2 P2 Task 2 — adds a right-side info island (portal target a page can render
// into via `infoIslandRef`), a draggable splitter, and a collapse/reopen tab.
// The provider wraps BOTH the shell markup and `children` so the page's portal
// and the shell's island share one context instance.
interface IslandShellProps {
  isDetailPage: boolean;
  topBarProps: React.ComponentProps<typeof TopBar>;
  sidebarProps: React.ComponentProps<typeof Sidebar>;
  children: React.ReactNode;
}

export function IslandShell(props: IslandShellProps) {
  return (
    <IslandWorkProvider>
      <IslandShellFrame {...props} />
    </IslandWorkProvider>
  );
}

function IslandShellFrame({ isDetailPage, topBarProps, sidebarProps, children }: IslandShellProps) {
  const { infoIslandRef, infoVisible, setInfoVisible, infoWidth, setInfoWidth, infoAvailable } =
    useIslandWork();

  // Drag the divider to resize the right info island. The island is on the RIGHT
  // edge, so width grows as the pointer moves left (innerWidth - clientX, minus
  // the frame's right padding). `setInfoWidth` clamps to 250–480. Listeners are
  // removed on pointerup so we never leak a live move handler.
  const startDrag = (e: React.PointerEvent) => {
    e.preventDefault();
    const handle = e.currentTarget as HTMLElement;
    const pointerId = e.pointerId;
    handle.setPointerCapture?.(pointerId);
    const onMove = (ev: PointerEvent) => setInfoWidth(window.innerWidth - ev.clientX - 12);
    const onUp = () => {
      handle.releasePointerCapture?.(pointerId);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  return (
    <div className="island-frame hidden sm:flex">
      <TopBar {...topBarProps} island />
      <div className="island-frame__row">
        {/* Nav island */}
        <div className="island-card relative flex-shrink-0">
          <Sidebar {...sidebarProps} island iconRail={isDetailPage} />
        </div>
        {/* Workspace island — wraps today's routed content unchanged */}
        <main className="island-card flex-1 min-w-0 overflow-auto">
          {children}
        </main>
        {/* Right info island — a page portals into infoIslandRef (spec §2) */}
        {infoVisible && (
          <>
            <div
              role="separator"
              aria-orientation="vertical"
              onPointerDown={startDrag}
              className="w-3 shrink-0 cursor-col-resize flex items-center justify-center group"
            >
              <span className="w-1 h-11 rounded bg-line-strong group-hover:bg-accent transition-colors" />
            </div>
            <aside className="island-card shrink-0 overflow-auto" style={{ width: infoWidth }}>
              <div ref={infoIslandRef} className="h-full" />
            </aside>
          </>
        )}
        {!infoVisible && infoAvailable && (
          <button className="island-reopen" onClick={() => setInfoVisible(true)}>‹ Info</button>
        )}
      </div>
    </div>
  );
}
