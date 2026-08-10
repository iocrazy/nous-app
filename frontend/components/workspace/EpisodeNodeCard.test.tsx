/**
 * EpisodeNodeCard (IA redesign Task 5) — the Overview accordion's single-node
 * info card. Pins: header entry button label follows `resolveSurface`,
 * read-only facts render `<span>`s, Complete-stage/Back only show on the
 * cursor node, and the entry button reports the full node object (so the
 * caller can route by surface via the existing `handleSelectNode`).
 *
 * Task 9 (角色门控行内编辑) adds the editable owner/schedule branch: when
 * `canEditConfig` is true, the owner fact renders as a real trigger button
 * that opens a candidate menu (people from `people`/agents from `agents`),
 * and the schedule fact renders as a trigger that opens `DateRangePopover`.
 * Both call `onPatchNode(nodeId, patch)` and fire-and-forget it — this
 * component holds NO optimistic/local override state and never
 * shows a toast itself. The optimistic-update / 403-revert / toast
 * responsibility lives one level up in `ProjectWorkspace`'s real
 * `onPatchNode` implementation (see ProjectWorkspace.test.tsx for that
 * coverage) — `onPatchNode`'s contract here is "never rejects" so this
 * component can safely `void` the call.
 */
import type { ComponentProps } from 'react';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { EpisodeNodeCard } from './EpisodeNodeCard';
import type { ProjectStageNode } from '../../types';
import type { PersonOption } from '../workflow/OwnerPicker';

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

const node: ProjectStageNode = {
  id: 'n2',
  project_id: '10',
  source_template_node_id: null,
  legacy_stage_id: null,
  name: 'Storyboard',
  sort_order: 1,
  parallel_group: null,
  status: 'pending',
  owner_user_id: null,
  owner_agent_id: null,
  planned_start: null,
  planned_due: null,
  review_required: false,
  deliverable_required: false,
  deliverable_label: 'Shot list + boards',
  deliverable_file_count: 0,
  skipped: false,
  surface: 'storyboard',
  members: [],
  completion_policy: 'owner',
  events: { notify_on_arrival: true, notify_on_complete: false, suggest_agent_run: false },
} as ProjectStageNode;

function cbs() {
  return {
    onEnterSurface: vi.fn(),
    onRequestAdvance: vi.fn(),
    onOpenTodolist: vi.fn(),
    onOpenSettings: vi.fn(),
  };
}

const PEOPLE: PersonOption[] = [
  { id: 'alice-uuid', name: 'Alice' },
  { id: 'bob-uuid', name: 'Bob' },
];

function renderCard(overrides: Partial<ComponentProps<typeof EpisodeNodeCard>> = {}) {
  const base = {
    node,
    canEditConfig: false,
    canWrite: true,
    isCursorNode: true,
    ...cbs(),
    ...overrides,
  };
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <EpisodeNodeCard {...base} />
    </I18nextProvider>,
  );
  return { ...utils, props: base };
}

afterEach(() => cleanup());

describe('EpisodeNodeCard', () => {
  it('renders name, status, entry button by surface, and readonly facts', () => {
    renderCard();
    expect(screen.getByText('Storyboard')).toBeTruthy();
    expect(screen.getByTestId('node-card-enter').textContent).toMatch(/Storyboard|Open/);
    expect(screen.getByTestId('node-card-owner').textContent).toMatch(/Unassigned/);
    expect(screen.getByTestId('node-card-owner').tagName).toBe('SPAN');
  });

  it('advance buttons only on cursor node', () => {
    const { rerender, props } = renderCard({ isCursorNode: true });
    expect(screen.getByTestId('node-card-advance')).toBeTruthy();
    rerender(
      <I18nextProvider i18n={makeI18n()}>
        <EpisodeNodeCard {...props} isCursorNode={false} />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('node-card-advance')).toBeNull();
  });

  // 评审修复轮1 (Important #1): a read-only member must never see
  // Back/Complete-stage, even on the cursor node — mirrors CurrentNodeCard's
  // `isActive && canWrite` gate (the pre-IA-redesign card this replaces).
  it('advance buttons hidden when canWrite is false, even on the cursor node', () => {
    renderCard({ isCursorNode: true, canWrite: false });
    expect(screen.queryByTestId('node-card-advance')).toBeNull();
  });

  it('enter button reports the node object', () => {
    const onEnterSurface = vi.fn();
    renderCard({ onEnterSurface });
    fireEvent.click(screen.getByTestId('node-card-enter'));
    expect(onEnterSurface).toHaveBeenCalledWith(node);
  });

  // ── entry-button label varies by surface ────────────────────────────────

  it('labels the entry button "Open Storyboard" for a storyboard-surface node', () => {
    renderCard({ node: { ...node, surface: 'storyboard' } });
    expect(screen.getByTestId('node-card-enter').textContent).toContain('Open Storyboard');
  });

  it('labels the entry button "Open Stage Board" for a deliverable-only (null-surface) node', () => {
    renderCard({ node: { ...node, surface: null } });
    expect(screen.getByTestId('node-card-enter').textContent).toContain('Open Stage Board');
  });

  it('labels the entry button "Open Script" for a script-surface node', () => {
    renderCard({ node: { ...node, surface: 'script' } });
    expect(screen.getByTestId('node-card-enter').textContent).toContain('Open Script');
  });

  it('labels the entry button "Open Renders" for a renders-surface node', () => {
    renderCard({ node: { ...node, surface: 'renders' } });
    expect(screen.getByTestId('node-card-enter').textContent).toContain('Open Renders');
  });

  // ── read-only facts beyond the brief's minimum ──────────────────────────

  it('shows the deliverable label and filed-file count when the node has one', () => {
    renderCard({ node: { ...node, deliverable_label: 'Shot list + boards', deliverable_file_count: 2 } });
    const deliverable = screen.getByTestId('node-card-deliverable');
    expect(deliverable.textContent).toContain('Shot list + boards');
    expect(deliverable.textContent).toContain('2');
  });

  it('omits the schedule row when neither planned_start nor planned_due is set', () => {
    renderCard({ node: { ...node, planned_start: null, planned_due: null } });
    expect(screen.queryByTestId('node-card-schedule')).toBeNull();
  });

  it('shows the schedule row once a planned date is set', () => {
    renderCard({ node: { ...node, planned_start: '2026-08-01', planned_due: null } });
    expect(screen.getByTestId('node-card-schedule').textContent).toContain('2026-08-01');
  });

  it('shows an agent-owner indicator when owner_agent_id is set', () => {
    renderCard({ node: { ...node, owner_agent_id: 'agent-1' } });
    expect(screen.getByTestId('node-card-owner').textContent).toMatch(/Agent/);
  });

  // ── footer actions ───────────────────────────────────────────────────────

  it('open-todolist button fires onOpenTodolist', () => {
    const onOpenTodolist = vi.fn();
    renderCard({ onOpenTodolist });
    fireEvent.click(screen.getByTestId('node-card-todolist'));
    expect(onOpenTodolist).toHaveBeenCalledTimes(1);
  });

  it('settings link fires onOpenSettings with the node id', () => {
    const onOpenSettings = vi.fn();
    renderCard({ onOpenSettings });
    fireEvent.click(screen.getByTestId('node-card-settings'));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
    expect(onOpenSettings.mock.calls[0][1]).toBe('n2');
  });

  it('back/complete buttons fire onRequestAdvance with their direction', () => {
    const onRequestAdvance = vi.fn();
    renderCard({ onRequestAdvance, isCursorNode: true });
    fireEvent.click(screen.getByTestId('node-card-back'));
    fireEvent.click(screen.getByTestId('node-card-complete'));
    expect(onRequestAdvance).toHaveBeenNthCalledWith(1, 'back');
    expect(onRequestAdvance).toHaveBeenNthCalledWith(2, 'forward');
  });

  // ── Task 9: role-gated inline edit (owner / schedule) ───────────────────

  it('editable owner renders as a button; clicking it opens a picker and selecting a person patches via onPatchNode', async () => {
    const onPatchNode = vi.fn().mockResolvedValue(undefined);
    renderCard({ canEditConfig: true, people: PEOPLE, onPatchNode });

    const ownerBtn = screen.getByTestId('node-card-owner');
    expect(ownerBtn.tagName).toBe('BUTTON');
    fireEvent.click(ownerBtn);
    fireEvent.click(await screen.findByText('Alice'));

    expect(onPatchNode).toHaveBeenCalledWith('n2', {
      owner_user_id: 'alice-uuid',
      owner_agent_id: null,
    });
  });

  it('empty editable fields render as "+ Assign"/"+ Set Schedule" pills', () => {
    renderCard({ canEditConfig: true, people: PEOPLE });
    expect(screen.getByTestId('node-card-owner').textContent).toMatch(/Assign/);
    expect(screen.getByTestId('node-card-schedule').textContent).toMatch(/Set Schedule/);
  });

  it('readonly viewer sees plain <span> facts even when owner/schedule are empty', () => {
    renderCard({ canEditConfig: false });
    expect(screen.getByTestId('node-card-owner').tagName).toBe('SPAN');
    // Read-only + empty schedule omits the row entirely (unchanged Task 5
    // behavior) — there is nothing editable to show a pill for.
    expect(screen.queryByTestId('node-card-schedule')).toBeNull();
  });

  it('editable schedule button opens DateRangePopover and a completed range patches via onPatchNode', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2026, 7, 1, 12, 0, 0)); // August 2026
    const onPatchNode = vi.fn().mockResolvedValue(undefined);
    renderCard({ canEditConfig: true, onPatchNode });

    fireEvent.click(screen.getByTestId('node-card-schedule'));
    fireEvent.click(screen.getByRole('button', { name: '2026-08-01' }));
    expect(onPatchNode).not.toHaveBeenCalled(); // first click only buffers a draft
    fireEvent.click(screen.getByRole('button', { name: '2026-08-05' }));

    expect(onPatchNode).toHaveBeenCalledWith('n2', {
      planned_start: '2026-08-01',
      planned_due: '2026-08-05',
    });
    vi.useRealTimers();
  });

  it('onPatchNode never rejecting is safe to fire-and-forget (no unhandled rejection) even when the caller-supplied promise rejects', async () => {
    // This component holds no local optimistic/revert state and shows no
    // toast itself — see the file-doc comment. A rejecting onPatchNode must
    // not throw synchronously or crash the click handler; the real
    // revert+toast contract is exercised in ProjectWorkspace.test.tsx
    // against the actual onPatchNode implementation.
    const onPatchNode = vi.fn().mockRejectedValue({ code: 'node_config_forbidden' });
    renderCard({ canEditConfig: true, people: PEOPLE, onPatchNode });
    fireEvent.click(screen.getByTestId('node-card-owner'));
    fireEvent.click(await screen.findByText('Alice'));
    expect(onPatchNode).toHaveBeenCalledTimes(1);
  });
});
