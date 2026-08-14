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

// Interpolating stand-in for i18next: the health line reads its relative time
// through t('common.time.minutesAgo', { count }), so a mock that drops
// variables would make "shows when the check ran" pass on an empty string.
const TRANSLATIONS: Record<string, string> = {
  'common.time.justNow': 'just now',
  'common.time.minutesAgo': '{{count}}m ago',
  'common.time.hoursAgo': '{{count}}h ago',
  'common.time.daysAgo': '{{count}}d ago',
  // Failure-reason wording lives only in the locale files (the component passes
  // no default for these — the key comes from a lookup table), so the mock has
  // to carry them or the reason would render as an empty string.
  'aiSettings.healthReason.timeout': 'Request timed out',
  'aiSettings.healthReason.rate_limit': 'Rate limited',
};
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, second?: unknown, third?: unknown): string => {
      const template = typeof second === 'string' ? second : (TRANSLATIONS[key] ?? '');
      const vars = ((typeof second === 'object' ? second : third) ?? {}) as Record<
        string,
        unknown
      >;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars[name] ?? ''));
    },
  }),
  // The health line reaches utils/relativeTime -> utils/formatDate -> i18n.ts,
  // which calls i18n.use(initReactI18next) at module-eval time: the named
  // export must exist on the mock or vitest throws before any test runs.
  initReactI18next: { type: '3rdParty', init: () => {} },
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

/**
 * The banner is the only place the user learns that editing a system template
 * writes to a personal layer instead of to the template. Two lines, one per
 * moment: the preventive one before there is anything to reset, the warn-toned
 * one (with the field count, so "changed one word" and "rewrote all three
 * documents" don't read alike) once there is.
 */
describe('AgentPersonaTab — persona hint banner', () => {
  const preset = (over: Partial<AILibraryAgent>): AILibraryAgent =>
    ({ ...agent, is_system_preset: true, ...over }) as AILibraryAgent;

  it('announces the override on a customized system preset', () => {
    const el = renderTab({
      agent: preset({ override_scope: 'user', override_fields: ['soul_md', 'model'] }),
    }).getByTestId('agent-override-banner');
    expect(el.getAttribute('data-override-count')).toBe('2');
    // Exclusive — the preventive line has nothing left to prevent.
    expect(screen.queryByTestId('agent-preset-hint-banner')).toBeNull();
  });

  it('explains where edits will land on a preset that has none yet', () => {
    renderTab({ agent: preset({ override_fields: [] }) });
    expect(screen.getByTestId('agent-preset-hint-banner')).toBeTruthy();
    expect(screen.queryByTestId('agent-override-banner')).toBeNull();
  });

  it('says nothing on a user-owned agent — its edits are the row itself', () => {
    renderTab({ agent: { ...agent, override_fields: ['soul_md'] } as AILibraryAgent });
    expect(screen.queryByTestId('agent-override-banner')).toBeNull();
    expect(screen.queryByTestId('agent-preset-hint-banner')).toBeNull();
  });
});

/**
 * Health of the SELECTED model, inline under the picker.
 *
 * Before this, a failing model was invisible here: the user picked it, saved,
 * and learned about it when the chat failed with nothing to go on. The line
 * carries the check time because the probe runs hourly — a green light from 50
 * minutes ago is not a statement about right now.
 */
describe('AgentPersonaTab — selected model health', () => {
  const health = {
    'mediahub-deepseek-v4-flash': { status: 'fail' as const, testedAt: null, code: null },
  };

  it('warns when the selected model failed its last health check', () => {
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-flash' },
      modelHealth: health,
    });
    const el = screen.getByTestId('model-health-warning');
    expect(el.textContent).toContain('may fail');
  });

  it('names the reason when the backend classified the failure', () => {
    // The point of the whole reason-code change: "timed out" (a local engine
    // still loading — usually wait) and "rate limited" (quota — go act) were
    // the same sentence under #1838, and they ask for opposite things.
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-flash' },
      modelHealth: {
        'mediahub-deepseek-v4-flash': { status: 'fail', testedAt: null, code: 'timeout' },
      },
    });
    expect(screen.getByTestId('model-health-warning').textContent).toContain(
      'Request timed out',
    );
  });

  it('keeps the plain wording for a failure with no code', () => {
    // Rows probed before the column existed. Saying "unknown reason" would be
    // inventing a diagnosis out of our own missing data.
    renderTab({ draft: { model: 'mediahub-deepseek-v4-flash' }, modelHealth: health });
    const text = screen.getByTestId('model-health-warning').textContent ?? '';
    expect(text).toContain('may fail');
    expect(text).not.toContain('(');
  });

  it('keeps the plain wording for a code this build cannot name', () => {
    // The backend enum can grow ahead of a frontend deploy; the user must never
    // see a raw key or an untranslated code string.
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-flash' },
      modelHealth: {
        'mediahub-deepseek-v4-flash': {
          status: 'fail',
          testedAt: null,
          code: 'quota_exhausted',
        },
      },
    });
    const text = screen.getByTestId('model-health-warning').textContent ?? '';
    expect(text).toContain('may fail');
    expect(text).not.toContain('quota_exhausted');
    expect(text).not.toContain('healthReason');
  });

  it('still lets the user keep an unhealthy model selected', () => {
    // #1838's rule, unchanged: the probe has produced a false negative in
    // production, so a red light advises and never vetoes. Nothing here
    // disables the picker or clears the draft's model.
    const updateDraft = vi.fn();
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-flash' },
      modelHealth: {
        'mediahub-deepseek-v4-flash': { status: 'fail', testedAt: null, code: 'rate_limit' },
      },
      updateDraft,
    });
    expect(screen.getByTestId('model-health-warning')).toBeTruthy();
    expect(updateDraft).not.toHaveBeenCalled();
  });

  it('stays quiet when the selected model is healthy', () => {
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-pro' },
      modelHealth: {
        ...health,
        'mediahub-deepseek-v4-pro': { status: 'ok' as const, testedAt: null, code: null },
      },
    });
    expect(screen.queryByTestId('model-health-warning')).toBeNull();
  });

  it('says nothing about a model that has never been probed', () => {
    // Silence, not a green light: an unprobed model has no health to report.
    renderTab({ draft: { model: 'byok-gpt-4o' }, modelHealth: health });
    expect(screen.queryByTestId('model-health-warning')).toBeNull();
    expect(screen.queryByTestId('model-health-ok')).toBeNull();
  });

  it('shows when the check ran, so a stale reading reads as stale', () => {
    const fortyMinutesAgo = new Date(Date.now() - 40 * 60_000).toISOString();
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-flash' },
      modelHealth: {
        'mediahub-deepseek-v4-flash': {
          status: 'fail',
          testedAt: fortyMinutesAgo,
          code: null,
        },
      },
    });
    expect(screen.getByTestId('model-health-warning').textContent).toContain('40m ago');
  });

  it('reports a healthy model check time too — quietly', () => {
    const tenMinutesAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    renderTab({
      draft: { model: 'mediahub-deepseek-v4-pro' },
      modelHealth: {
        'mediahub-deepseek-v4-pro': { status: 'ok', testedAt: tenMinutesAgo, code: null },
      },
    });
    expect(screen.getByTestId('model-health-ok').textContent).toContain('10m ago');
  });
});
