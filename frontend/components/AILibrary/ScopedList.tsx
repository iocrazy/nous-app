// frontend/components/AILibrary/ScopedList.tsx
// Shared list primitive for the AI Library — groups items by scope
// (System Presets → My Private → each Team → each Project) and renders
// each section with a sticky header. Both AgentsTab (sidebar list) and
// SkillsTab (card grid) reuse this; they differ only in the render
// delegates passed in.
//
// Usage — items must expose {team_id, project_id, team_name, project_name}
// plus a ``isSystemPreset(item)`` predicate so the generic grouping can
// place each row. Callers render their own item component via
// ``renderItem`` and own their own section wrapper via ``renderSection``.

import React from 'react';

/** Minimum contract an item must satisfy to be grouped by scope. */
export interface Scoped {
  team_id?: number | null;
  project_id?: number | null;
  team_name?: string | null;
  project_name?: string | null;
}

/** One rendered section in the output. */
export interface ScopedGroup<T> {
  /** React key + stable identifier for test selectors. NOT user-visible. */
  key: string;
  /** Translated label shown as the sticky header. */
  label: string;
  items: T[];
}

export interface ScopedLabels {
  systemPresets: string;
  privateLabel: string;
  teamLabel: (name: string) => string;
  projectLabel: (name: string) => string;
}

/**
 * Slot items into scope-derived groups. Ordering:
 *   1. System Presets (``isSystemPreset(item) === true``)
 *   2. My Private     (no team, no project, not preset)
 *   3. Each Team      (one group per distinct team_id)
 *   4. Each Project   (one group per distinct project_id)
 *
 * Exported separately so tabs can use this logic without mounting the
 * React component (keeps existing unit tests happy).
 */
export function groupByScope<T extends Scoped>(
  items: T[],
  isSystemPreset: (item: T) => boolean,
  labels: ScopedLabels,
): ScopedGroup<T>[] {
  const presets: T[] = [];
  const privateOnes: T[] = [];
  const byTeam = new Map<number, { name: string; items: T[] }>();
  const byProject = new Map<number, { name: string; items: T[] }>();

  for (const item of items) {
    if (isSystemPreset(item)) {
      presets.push(item);
      continue;
    }
    if (item.team_id != null) {
      const entry = byTeam.get(item.team_id) ?? {
        name: item.team_name ?? String(item.team_id),
        items: [],
      };
      entry.items.push(item);
      byTeam.set(item.team_id, entry);
      continue;
    }
    if (item.project_id != null) {
      const entry = byProject.get(item.project_id) ?? {
        name: item.project_name ?? String(item.project_id),
        items: [],
      };
      entry.items.push(item);
      byProject.set(item.project_id, entry);
      continue;
    }
    privateOnes.push(item);
  }

  const groups: ScopedGroup<T>[] = [];
  if (presets.length > 0) {
    groups.push({ key: 'system', label: labels.systemPresets, items: presets });
  }
  if (privateOnes.length > 0) {
    groups.push({ key: 'private', label: labels.privateLabel, items: privateOnes });
  }
  for (const [teamId, entry] of byTeam) {
    groups.push({
      key: `team:${teamId}`,
      label: labels.teamLabel(entry.name),
      items: entry.items,
    });
  }
  for (const [projectId, entry] of byProject) {
    groups.push({
      key: `project:${projectId}`,
      label: labels.projectLabel(entry.name),
      items: entry.items,
    });
  }
  return groups;
}

/**
 * Layout preset for the sticky section header. Callers pick:
 *   - ``sidebar``: narrow rail with hairline border (AgentsTab style).
 *   - ``section``: wide header above a card grid (SkillsTab style).
 *
 * Exposed as a prop so the two tabs stay pixel-equivalent with the
 * hand-rolled code they had before — this is a pure refactor.
 */
export type ScopedHeaderVariant = 'sidebar' | 'section';

const HEADER_CLASSNAMES: Record<ScopedHeaderVariant, string> = {
  sidebar:
    'sticky top-0 z-10 bg-zinc-950/95 px-4 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500 border-b border-zinc-800/60',
  section:
    'sticky top-0 z-10 -mx-6 mb-3 border-b border-zinc-800/60 bg-zinc-950/95 px-6 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500',
};

export interface ScopedListProps<T extends Scoped> {
  items: T[];
  isSystemPreset: (item: T) => boolean;
  labels: ScopedLabels;
  /** Render a single item. The caller controls item styling + click handling. */
  renderItem: (item: T) => React.ReactNode;
  /**
   * Wrap the items inside a section body. Receives the group + pre-rendered
   * item nodes. Needed because AgentsTab renders a flat button stack while
   * SkillsTab renders a grid with multiple columns.
   */
  renderGroupBody?: (group: ScopedGroup<T>, children: React.ReactNode) => React.ReactNode;
  headerVariant: ScopedHeaderVariant;
}

/**
 * Generic scope-grouped list renderer. See ``ScopedListProps`` for the
 * customization hooks.
 */
export function ScopedList<T extends Scoped>({
  items,
  isSystemPreset,
  labels,
  renderItem,
  renderGroupBody,
  headerVariant,
}: ScopedListProps<T>): React.ReactElement {
  const groups = groupByScope(items, isSystemPreset, labels);
  const headerClass = HEADER_CLASSNAMES[headerVariant];

  return (
    <>
      {groups.map((group) => {
        const itemNodes = group.items.map((item) => (
          <React.Fragment key={itemKey(item)}>{renderItem(item)}</React.Fragment>
        ));
        return (
          <section key={group.key}>
            <div className={headerClass}>{group.label}</div>
            {renderGroupBody ? renderGroupBody(group, itemNodes) : itemNodes}
          </section>
        );
      })}
    </>
  );
}

/**
 * Extract a stable React key from an item. Falls back to JSON.stringify for
 * items without id/slug — callers normally pass typed rows so this is
 * defensive.
 */
function itemKey(item: unknown): string {
  if (item && typeof item === 'object') {
    const asRec = item as Record<string, unknown>;
    if (typeof asRec.slug === 'string') return asRec.slug;
    if (typeof asRec.id === 'string' || typeof asRec.id === 'number') {
      return String(asRec.id);
    }
  }
  return JSON.stringify(item);
}

export default ScopedList;
