/**
 * CurrentNodeCard — suggest-agent-run chip (Project Workflow M2, Task E3).
 *
 * The chip is a pure navigation shortcut: it only renders when the node's
 * `events.suggest_agent_run` flag is on AND the node has an agent owner, and
 * clicking it fires the exact same handler as "Open in Todolist" — it never
 * calls a dispatch API itself (that gate lives in the Todolist run-confirm
 * flow). See task-E3-brief.md.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { CurrentNodeCard } from './CurrentNodeCard';
import type { AgentOption, PersonOption } from './OwnerPicker';
import type { ProjectStageNode } from '../../types';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

function node(over: Partial<ProjectStageNode>): ProjectStageNode {
  return {
    id: '1',
    project_id: '10',
    source_template_node_id: null,
    legacy_stage_id: null,
    name: 'Script',
    sort_order: 0,
    parallel_group: null,
    status: 'in_progress',
    owner_user_id: null,
    owner_agent_id: null,
    planned_start: null,
    planned_due: null,
    review_required: false,
    deliverable_required: false,
    deliverable_label: null,
    skipped: false,
    members: [],
    completion_policy: 'owner',
    events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
    ...over,
  };
}

const people: PersonOption[] = [];
const agents: AgentOption[] = [{ id: 'agent-1', name: 'Script Bot' }];

function renderCard(
  overrides: Partial<ProjectStageNode>,
  opts: { onOpenTodolist?: () => void } = {},
) {
  const onOpenTodolist = opts.onOpenTodolist ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <CurrentNodeCard
        projectId="10"
        node={node(overrides)}
        canWrite={false}
        people={people}
        agents={agents}
        onPatched={vi.fn()}
        onRequestAdvance={vi.fn()}
        onOpenTodolist={onOpenTodolist}
      />
    </I18nextProvider>,
  );
  return { ...utils, onOpenTodolist };
}

describe('CurrentNodeCard — suggest-agent-run chip', () => {
  it('renders with the agent name when both the flag and an agent owner are set', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Script Bot',
    );
  });

  it('falls back to generic copy when the agent owner is not in the known agents list', () => {
    renderCard({
      owner_agent_id: 'agent-unknown',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run agent',
    );
  });

  it('hides the chip when suggest_agent_run is false, even with an agent owner', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: false },
    });
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('hides the chip when the flag is on but the owner is a person, not an agent', () => {
    renderCard({
      owner_user_id: 'user-1',
      owner_agent_id: null,
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('hides the chip when neither flag nor agent owner is set', () => {
    renderCard({});
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('clicking the chip fires the same handler as Open in Todolist', () => {
    const onOpenTodolist = vi.fn();
    renderCard(
      {
        owner_agent_id: 'agent-1',
        events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
      },
      { onOpenTodolist },
    );
    fireEvent.click(screen.getByTestId('workflow-suggest-agent-chip'));
    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
  });
});
