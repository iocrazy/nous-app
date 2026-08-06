/**
 * AgentActionBar — the overflow menu's "Reset to defaults" item (mig 341).
 *
 * Resetting a personal override is rare and destructive-ish, so it lives in
 * the "..." menu next to Duplicate / Delete rather than in the action row.
 * What these tests pin is when it may appear at all: only for a system preset
 * the caller has actually customized. Offering it on a pristine preset would
 * delete nothing; offering it on a user-owned agent would suggest the row can
 * fall back to a template it never had.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { AILibraryAgent } from '../../types';

const getAgentStatus = vi.fn(async () => ({
  status: 'idle' as const,
  paused_reason: null,
  running_count: 0,
}));
vi.mock('../../services/aiLibraryService', () => ({
  aiLibraryService: {
    getAgentStatus: (...a: unknown[]) => getAgentStatus(...(a as [])),
    pauseAgent: vi.fn(),
    resumeAgent: vi.fn(),
  },
}));

vi.mock('../../services/issuesService', () => ({ createIssue: vi.fn() }));
vi.mock('../Todolist/NewIssueDialog', () => ({ NewIssueDialog: () => <div /> }));

const addToast = vi.fn();
vi.mock('../Toast', () => ({ useToast: () => ({ addToast }) }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>) =>
      typeof d === 'string' ? d : '',
  }),
}));

import { AgentActionBar } from './AgentActionBar';

const agent = (over: Partial<AILibraryAgent>): AILibraryAgent =>
  ({ id: 'a1', slug: 'script-ai', name: 'Script AI', ...over }) as AILibraryAgent;

const PRESET_WITH_OVERRIDE = agent({
  is_system_preset: true,
  override_scope: 'user',
  override_fields: ['soul_md', 'model'],
});

const onResetOverride = vi.fn();

function renderBar(over: Partial<React.ComponentProps<typeof AgentActionBar>> = {}) {
  return render(
    <AgentActionBar
      agent={PRESET_WITH_OVERRIDE}
      readOnly
      onAgentUpdated={vi.fn()}
      onResetOverride={onResetOverride}
      {...over}
    />,
  );
}

/** The menu is closed until "..." is clicked. */
function openMenu(): void {
  fireEvent.click(screen.getByLabelText('More'));
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AgentActionBar — reset override', () => {
  it('offers Reset to defaults for a customized system preset', () => {
    renderBar();
    openMenu();
    expect(screen.getByTestId('agent-reset-override')).toBeTruthy();
  });

  it('invokes the reset handler when the item is clicked, and closes the menu', () => {
    renderBar();
    openMenu();
    fireEvent.click(screen.getByTestId('agent-reset-override'));
    expect(onResetOverride).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('agent-reset-override')).toBeNull();
  });

  it('hides it on a preset with no override — nothing to reset', () => {
    renderBar({ agent: agent({ is_system_preset: true, override_fields: [] }) });
    openMenu();
    expect(screen.queryByTestId('agent-reset-override')).toBeNull();
  });

  it('hides it on a user-owned agent, override payload or not', () => {
    renderBar({
      agent: agent({ is_system_preset: false, override_fields: ['soul_md'] }),
    });
    openMenu();
    expect(screen.queryByTestId('agent-reset-override')).toBeNull();
  });

  it('hides it when the parent passes no handler', () => {
    renderBar({ onResetOverride: undefined });
    openMenu();
    expect(screen.queryByTestId('agent-reset-override')).toBeNull();
  });
});
