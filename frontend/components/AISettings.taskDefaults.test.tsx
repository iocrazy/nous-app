/**
 * Tests for the Task Assignment agent picker's "unset" state.
 *
 * Bug: renderAgentSelect rendered no option whose value was '', so when
 * task_assignment[taskKey] was unset UiSelect fell through to the first option
 * in the list (alphabetically "[System] Analyze"). Every unassigned task looked
 * assigned to Analyze, and picking Analyze fired no change event — the phantom
 * assignment could not even be saved over.
 *
 * Fix: an explicit `<option value="">` labelled after the agent the BACKEND
 * actually falls back to (DEFAULT_*_AGENT_SLUG in
 * backend/app/services/ai/providers/ai_provider_helpers.py).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';

import en from '../public/locales/en.json';

// Resolve against the REAL shipped English copy rather than a hand-written
// table, so a missing/renamed key surfaces here as a failing assertion instead
// of a raw `aiSettings.someKey` reaching users. The component's own i18n
// instance is never initialized in tests (nothing loads i18n.ts), so
// react-i18next would otherwise hand back bare keys.
vi.mock('react-i18next', () => {
  // Created once by the factory so `t` is referentially stable across renders,
  // exactly like the real react-i18next hook. An unstable `t` would make any
  // hook that lists it as a dependency re-fire on every render.
  const t = (key: string, vars?: Record<string, unknown>): string => {
    const template = key
      .split('.')
      .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
    if (typeof template !== 'string') return key;
    return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
  };
  return { useTranslation: () => ({ t }) };
});

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn().mockResolvedValue(undefined),
  testAIConnection: vi.fn(),
  getNousModels: vi.fn().mockResolvedValue([]),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
  }),
  GOVERNANCE_ALL_ALLOWED: {
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
  },
}));
// Analyze is listed FIRST on purpose — it is the option the broken picker
// snapped to, so its presence here is what makes the regression detectable.
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: {
    listAgents: vi.fn().mockResolvedValue([
      { id: '1', slug: 'analyze', name: 'Analyze', model: 'doubao', temperature: 0.7, max_tokens: 4096, is_system_preset: true },
      { id: '2', slug: 'caption', name: 'Caption', model: 'doubao', temperature: 0.7, max_tokens: 4096, is_system_preset: true },
      { id: '3', slug: 'classify', name: 'Classify', model: 'doubao', temperature: 0.7, max_tokens: 4096, is_system_preset: true },
      { id: '4', slug: 'summarize', name: 'Summarize', model: 'doubao', temperature: 0.7, max_tokens: 4096, is_system_preset: true },
    ]),
  },
}));
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));
vi.mock('../features/canvas-core/smart/NousCenterVerifyPanel', () => ({
  NousCenterVerifyPanel: () => null,
}));

const baseSettings: AISettingsType = {
  ai_enabled: true,
  auto_transcribe: false,
  auto_summarize: false,
  preferred_language: 'auto',
  providers: {},
  task_assignment: {
    transcription: '',
    summarization: '',
    visual_analysis: '',
    translation: '',
    caption: '',
    classification: '',
    image_generation: '',
    script_generation: '',
  },
};

/** The UiSelect trigger inside the row carrying `label`. */
function triggerForRow(label: string): HTMLElement {
  const row = screen.getByText(label).closest('div')?.parentElement;
  if (!row) throw new Error(`row not found for ${label}`);
  const trigger = row.querySelector('button[aria-haspopup="listbox"]');
  if (!trigger) throw new Error(`select trigger not found for ${label}`);
  return trigger as HTMLElement;
}

describe('AISettings task assignment — unset shows a Default option', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the backend default agent, not the first agent, when caption is unassigned', async () => {
    render(<AISettings settings={baseSettings} onSave={vi.fn()} />);

    await waitFor(() => {
      expect(triggerForRow('Caption (Image → Prompt)')).toHaveTextContent(
        'Default — [System] Caption'
      );
    });
    expect(triggerForRow('Caption (Image → Prompt)')).not.toHaveTextContent('Analyze');
  });

  it('labels the classification default after the backend `classify` agent', async () => {
    render(<AISettings settings={baseSettings} onSave={vi.fn()} />);

    await waitFor(() => {
      expect(triggerForRow('Classification (Auto Tag)')).toHaveTextContent(
        'Default — [System] Classify'
      );
    });
  });

  it('labels the summarization default after the backend `summarize` agent', async () => {
    // 2026-08-20 收口:摘要此前不解析任何 agent(工作流扫 provider 优先级),
    // 所以这一行刻意不在 TASK_DEFAULT_AGENT_SLUG 里、显示的是泛化的
    // "Default (system)"。现在它走 resolve_task_ai_config,后端兜底就是
    // `summarize` 预设,标签必须如实说出来。
    render(<AISettings settings={baseSettings} onSave={vi.fn()} />);

    await waitFor(() => {
      expect(triggerForRow('Summarization')).toHaveTextContent(
        'Default — [System] Summarize'
      );
    });
  });

  it('keeps showing the assigned agent when the task IS assigned', async () => {
    render(
      <AISettings
        settings={{
          ...baseSettings,
          task_assignment: { ...baseSettings.task_assignment, caption: 'analyze' },
        }}
        onSave={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(triggerForRow('Caption (Image → Prompt)')).toHaveTextContent('[System] Analyze');
    });
    expect(triggerForRow('Caption (Image → Prompt)')).not.toHaveTextContent('Default');
  });
});
