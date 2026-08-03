// frontend/components/AILibrary/sidebarNav.ts
// AI Library secondary-sidebar structure — B0 (spec 2026-08-02 §B0).
//
// The sidebar used to carry ~30 rows: 19 agents and the skills tree flattened
// one-per-line. Navigation for those now lives in the content area (the
// gallery's cards), so the sidebar is a fixed set of high-frequency entries.
//
// Kept as data rather than JSX so the shape is testable — sidebarNav.test.ts
// pins the item count and that no agent/skill row ever creeps back in.

export interface AILibraryNavItem {
  /** Stable id — also the lucide icon lookup key in the component. */
  key: string;
  labelKey: string;
  labelDefault: string;
  /** Path suffix appended to the team urlPrefix. */
  path: string;
  /** Matches the location pathname when this entry is the active one. */
  activePattern: RegExp;
}

export interface AILibraryNavSection {
  key: string;
  /** Absent for the lead section, which renders without a group heading. */
  labelKey?: string;
  labelDefault?: string;
  items: AILibraryNavItem[];
}

export const AI_LIBRARY_NAV: AILibraryNavSection[] = [
  {
    key: 'library',
    items: [
      {
        key: 'library',
        labelKey: 'sidebar.aiLibrary',
        labelDefault: 'AI Library',
        path: '/ai-library',
        // Exact match only — the gallery is the index route, so any deeper
        // path belongs to an agent/skill detail view, not to this entry.
        activePattern: /\/ai-library\/?$/,
      },
    ],
  },
  {
    key: 'runtime',
    labelKey: 'aiLibrary.runtimeSection',
    labelDefault: 'Runtime',
    items: [
      {
        key: 'workforce',
        labelKey: 'sidebar.workforce',
        labelDefault: 'Workforce',
        path: '/ai-library/workforce',
        activePattern: /\/ai-library\/workforce(\/|$)/,
      },
    ],
  },
  {
    key: 'chat',
    labelKey: 'aiLibrary.chatSection',
    labelDefault: 'Chat',
    items: [
      {
        key: 'sessions',
        labelKey: 'sidebar.sessions',
        labelDefault: 'Sessions',
        path: '/ai-library/sessions',
        activePattern: /\/ai-library\/sessions(\/|$)/,
      },
    ],
  },
  {
    key: 'insights',
    labelKey: 'aiLibrary.insightsSection',
    labelDefault: 'Insights',
    items: [
      {
        key: 'usage',
        labelKey: 'sidebar.aiUsage',
        labelDefault: 'AI Usage',
        path: '/ai-library/usage',
        activePattern: /\/ai-library\/usage(\/|$)/,
      },
      {
        key: 'aiCost',
        labelKey: 'sidebar.aiCost',
        labelDefault: 'Cost & Budget',
        path: '/ai-library/ai-cost',
        activePattern: /\/ai-library\/ai-cost(\/|$)/,
      },
      {
        key: 'memory',
        labelKey: 'sidebar.memory',
        labelDefault: 'My Memory',
        path: '/ai-library/memory',
        activePattern: /\/ai-library\/memory(\/|$)/,
      },
    ],
  },
];

/** Flat view of every static entry, in render order. */
export function allNavItems(): AILibraryNavItem[] {
  return AI_LIBRARY_NAV.flatMap((section) => section.items);
}

/**
 * Which entry (if any) the current pathname activates.
 *
 * Returns at most one key: the patterns are mutually exclusive by
 * construction (the library entry anchors to end-of-string), so a deep path
 * like /ai-library/agents/script_ai activates nothing — the gallery entry
 * must not look selected while the user is inside a detail view.
 */
export function activeNavKey(pathname: string): string | null {
  return allNavItems().find((item) => item.activePattern.test(pathname))?.key ?? null;
}
