import React from 'react';
import { TopBar } from './TopBar';
import { Sidebar } from './Sidebar';

// Island redesign v2 P1b — desktop app shell. Background micro-glow + a
// full-width global topbar strip, then a content row with a floating nav
// island and a workspace island. The heavy children (TopBar/Sidebar) keep all
// their logic; we only place them inside island cards and pass `island`. Detail
// routes pin the 54px icon rail (spec D5). Mobile chrome is rendered by
// AppLayout outside this shell and is unaffected (this frame is `hidden sm:flex`).
interface IslandShellProps {
  isDetailPage: boolean;
  topBarProps: React.ComponentProps<typeof TopBar>;
  sidebarProps: React.ComponentProps<typeof Sidebar>;
  children: React.ReactNode;
}

export function IslandShell({ isDetailPage, topBarProps, sidebarProps, children }: IslandShellProps) {
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
      </div>
    </div>
  );
}
