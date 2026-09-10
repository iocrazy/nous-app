/**
 * Issue detail block registry (harness P4 seam C). The page is three zones —
 * cockpit / timeline / context — each a list of blocks picked by `match`
 * against the issue's rollup and ordered by `order`. A new origin kind or a
 * new capability adds a block here; the page component never grows a branch.
 * Enumerable so tests can assert what renders for a given issue.
 */

import type { ComponentType } from 'react';

import type { IssueProgress } from '../../services/issuesService';

export type IssueBlockZone = 'cockpit' | 'timeline' | 'context';

/** Page-level handles a block may need beyond the issue itself. Optional so
 * matchers can be tested with literals; blocks degrade when a field is absent. */
export interface IssueBlockEnv {
  agentsById?: Record<string, { id: string; name?: string | null }>;
  projectPath?: string | null;
  subtaskCount?: { total: number; done: number } | null;
  assigneeName?: string;
  refreshKey?: number;
  /** Bumped when this issue's schedules change (harness 2b-2 §5-2). */
  schedulesRefreshKey?: number;
  teamId?: string;
  /** Ask the page to re-read the issue + rollup (after a PATCH, a cancel …). */
  onIssueChanged?: () => void;
  /** Phase 2a: answer the parked typed question (label or free text +
   *  question id). Provided by the detail page; the cockpit only mounts the
   *  card when it is there. */
  onAnswerQuestion?: (value: string, answerTo: string) => Promise<void>;
}

/** What `match` sees. Kept structural so blocks can be tested with literals. */
export interface IssueBlockContext {
  issue: Record<string, unknown>;
  /** issue.rollup from GET /issues/{id}/progress; null until loaded. */
  rollup: IssueProgress | null;
  originKind: string | null;
  phase: string | null;
  env: IssueBlockEnv;
}

export interface IssueBlockProps {
  ctx: IssueBlockContext;
}

export interface IssueBlock {
  id: string;
  zone: IssueBlockZone;
  order: number;
  match: (ctx: IssueBlockContext) => boolean;
  component: ComponentType<IssueBlockProps>;
}

const blocks = new Map<string, IssueBlock>();

export function registerIssueBlock(block: IssueBlock): void {
  if (blocks.has(block.id)) throw new Error(`issue block "${block.id}" already registered`);
  blocks.set(block.id, block);
}

export function blocksFor(zone: IssueBlockZone, ctx: IssueBlockContext): IssueBlock[] {
  return [...blocks.values()]
    .filter((b) => b.zone === zone)
    .filter((b) => {
      try {
        return b.match(ctx);
      } catch {
        return false; // a broken matcher hides its own block, never the page
      }
    })
    .sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function registeredIssueBlocks(): string[] {
  return [...blocks.keys()].sort();
}

/** Tests only. */
export function __resetIssueBlocks(): void {
  blocks.clear();
}

/** Build the matcher input from an issue row + its rollup. */
export function issueBlockContext(
  issue: Record<string, unknown>,
  rollup: IssueProgress | null,
  env: IssueBlockEnv = {},
): IssueBlockContext {
  const raw = issue.raw as Record<string, unknown> | undefined;
  const origin = rollup?.origin?.kind ?? issue.origin_kind ?? raw?.origin_kind;
  return {
    issue,
    rollup,
    originKind: typeof origin === 'string' ? origin : null,
    phase: typeof rollup?.phase === 'string' ? rollup.phase : null,
    env,
  };
}
