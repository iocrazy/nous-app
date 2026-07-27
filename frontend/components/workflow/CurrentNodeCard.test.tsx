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

  it('falls back to the i18n generic-agent copy when the agent owner is not in the known agents list', () => {
    // Regression guard (#final-review E3): this fallback used to be a hardcoded
    // English literal ('agent') interpolated straight into the (possibly
    // Chinese) translated sentence — now it comes from
    // projects.workflow.genericAgent so a zh locale gets a real translation
    // instead of a stray English word.
    renderCard({
      owner_agent_id: 'agent-unknown',
      events: { notify_on_arrival: false, notify_on_complete: false, suggest_agent_run: true },
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Agent',
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

describe('CurrentNodeCard — Run now chip (M3 Task H3)', () => {
  it('renders the solid Run now button, not the suggest text chip, once metadata.run_prepared_at is set', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: true,
        prepare_agent_run: true,
      },
      metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
    });
    expect(screen.getByTestId('workflow-run-now-chip')).toHaveTextContent('Run now');
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('keeps the plain suggest text chip when suggest_agent_run is on but the hook has not fired yet (no run_prepared_at)', () => {
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: true,
        prepare_agent_run: true,
      },
      // metadata omitted entirely — normalizeInstanceNode would default this to
      // {} in real data; the raw node() fixture leaves it undefined, which the
      // component must tolerate the same way (`node.metadata?.run_prepared_at`).
    });
    expect(screen.getByTestId('workflow-suggest-agent-chip')).toHaveTextContent(
      'Suggested: run Script Bot',
    );
    expect(screen.queryByTestId('workflow-run-now-chip')).toBeNull();
  });

  it('never renders the Run now button when suggest_agent_run is off, even with run_prepared_at set', () => {
    // Guards against a hook race: metadata could in principle still carry a
    // stale run_prepared_at from before the template toggle was flipped off —
    // the base gate (suggest_agent_run && owner_agent_id) must still win.
    renderCard({
      owner_agent_id: 'agent-1',
      events: {
        notify_on_arrival: false,
        notify_on_complete: false,
        suggest_agent_run: false,
        prepare_agent_run: true,
      },
      metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
    });
    expect(screen.queryByTestId('workflow-run-now-chip')).toBeNull();
    expect(screen.queryByTestId('workflow-suggest-agent-chip')).toBeNull();
  });

  it('clicking Run now fires onOpenTodolist — CurrentNodeCard has no mirror-issue id to open DispatchConfirmDialog directly, so it routes through the same handler as Open in Todolist (never calls dispatch itself)', () => {
    const onOpenTodolist = vi.fn();
    renderCard(
      {
        owner_agent_id: 'agent-1',
        events: {
          notify_on_arrival: false,
          notify_on_complete: false,
          suggest_agent_run: true,
          prepare_agent_run: true,
        },
        metadata: { run_prepared_at: '2026-07-27T00:00:00+00:00' },
      },
      { onOpenTodolist },
    );
    fireEvent.click(screen.getByTestId('workflow-run-now-chip'));
    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
  });
});
