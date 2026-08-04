/**
 * AgentPersonaTab — the skill bindings (spec §03).
 *
 * Binding used to be a hunt across two lists ("Bound Skills" above,
 * "Available Skills" below) with Add / Remove buttons. One row per skill with
 * a toggle says the same thing in one place — and makes "is this one on?"
 * answerable without scrolling to find which list it landed in.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { AILibraryAgent, AILibrarySkill } from '../../types';

vi.mock('./MarkdownEditor', () => ({ MarkdownEditor: () => <div /> }));
vi.mock('./AgentIconPicker', () => ({ AgentIconPicker: () => <div /> }));
vi.mock('./agentEditorModel', () => ({ renderModelSelect: () => <div /> }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (_k: string, d?: string | Record<string, unknown>) =>
      typeof d === 'string' ? d : '',
  }),
}));

import { AgentPersonaTab } from './AgentPersonaTab';

const agent = { id: 'a1', slug: 'script-ai', name: 'Script AI' } as AILibraryAgent;

const skill = (id: number, name: string): AILibrarySkill =>
  ({ id, name, slug: `s-${id}`, is_public: false, team_id: null, project_id: null }) as AILibrarySkill;

const ALL = [skill(1, 'Outline'), skill(2, 'Branch'), skill(3, 'Expand')];

const onAddSkill = vi.fn();
const onRemoveSkill = vi.fn();
const onMoveSkill = vi.fn();

function renderTab(over: Partial<React.ComponentProps<typeof AgentPersonaTab>> = {}) {
  return render(
    <MemoryRouter>
      <AgentPersonaTab
        agent={agent}
        draft={{}}
        updateDraft={vi.fn()}
        readOnly={false}
        catalogLocked={false}
        modelGroups={[]}
        localSkillIds={[1]}
        allSkills={ALL}
        skillsLoading={false}
        onAddSkill={onAddSkill}
        onRemoveSkill={onRemoveSkill}
        onMoveSkill={onMoveSkill}
        {...over}
      />
    </MemoryRouter>,
  );
}

describe('AgentPersonaTab — 技能绑定开关行', () => {
  beforeEach(() => {
    onAddSkill.mockReset();
    onRemoveSkill.mockReset();
    onMoveSkill.mockReset();
  });

  it('lists every accessible skill once, bound or not', () => {
    renderTab();
    expect(screen.getAllByTestId('skill-toggle-row')).toHaveLength(3);
  });

  it('puts bound skills first, in composition order', () => {
    renderTab({ localSkillIds: [3, 1] });
    const rows = screen.getAllByTestId('skill-toggle-row');
    expect(rows[0].textContent).toContain('Expand');
    expect(rows[1].textContent).toContain('Outline');
    expect(rows[0].getAttribute('data-bound')).toBe('true');
    expect(rows[2].getAttribute('data-bound')).toBe('false');
  });

  it('reflects bound state on the switch, not just in the ordering', () => {
    renderTab();
    const switches = screen.getAllByRole('switch');
    expect(switches[0].getAttribute('aria-checked')).toBe('true');
    expect(switches[1].getAttribute('aria-checked')).toBe('false');
  });

  it('binds an unbound skill when its switch is turned on', () => {
    renderTab();
    fireEvent.click(screen.getByRole('switch', { name: 'Branch' }));
    expect(onAddSkill).toHaveBeenCalledWith(2);
    expect(onRemoveSkill).not.toHaveBeenCalled();
  });

  it('unbinds a bound skill when its switch is turned off', () => {
    renderTab();
    fireEvent.click(screen.getByRole('switch', { name: 'Outline' }));
    expect(onRemoveSkill).toHaveBeenCalledWith(1);
    expect(onAddSkill).not.toHaveBeenCalled();
  });

  it('disables every switch when the catalog is locked', () => {
    // Bindings are gated by `catalogLocked` (admin-owned catalog), not by the
    // persona-document `readOnly` — an official template's prompts and its
    // skill set are locked by different rules.
    renderTab({ catalogLocked: true });
    for (const s of screen.getAllByRole('switch')) {
      expect((s as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it('leaves switches live when only the persona documents are read-only', () => {
    renderTab({ readOnly: true, catalogLocked: false });
    expect((screen.getAllByRole('switch')[0] as HTMLButtonElement).disabled).toBe(false);
  });

  it('hides reorder controls when a single skill is bound — no order to change', () => {
    renderTab({ localSkillIds: [1] });
    expect(screen.queryByLabelText('Move up')).toBeNull();
  });

  it('offers reorder on bound rows once order is meaningful', () => {
    renderTab({ localSkillIds: [1, 2] });
    // Two bound rows → two up + two down, and the first row's up is disabled.
    const ups = screen.getAllByLabelText('Move up') as HTMLButtonElement[];
    expect(ups).toHaveLength(2);
    expect(ups[0].disabled).toBe(true);
    fireEvent.click(ups[1]);
    expect(onMoveSkill).toHaveBeenCalledWith(2, -1);
  });

  it('never offers reorder on an unbound row — it has no position', () => {
    renderTab({ localSkillIds: [1, 2] });
    const rows = screen.getAllByTestId('skill-toggle-row');
    const unbound = rows.find((r) => r.getAttribute('data-bound') === 'false')!;
    expect(unbound.querySelector('[aria-label="Move up"]')).toBeNull();
  });
});
