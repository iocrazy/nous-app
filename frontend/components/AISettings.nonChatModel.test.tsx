/**
 * BYOK non-chat model guard in AI Settings (2026-08-16 production incident).
 *
 * A user pruned the doubao provider card's enabled models down to
 * `doubao-embedding-vision-251215`. Summarization resolves "first keyed+enabled
 * provider, then THAT provider's selected_model", so every summary afterwards
 * POSTed an embedding model to /chat/completions and failed — and this page,
 * where the choice was made, said nothing at all.
 *
 * The guard marks and never blocks: a BYOK catalog has no type field, so the
 * warning is a naming guess and the user may know better.
 *
 * ── Where the red belongs (2026-09-15) ─────────────────────────────────────
 * The first version painted every suspect chip danger-red with a warning
 * triangle. A user whose doubao card legitimately holds embedding, image AND
 * chat models — this product needs all three — saw three red rows on a working
 * configuration and asked what was broken. Nothing was: the selected model was
 * the chat one, and the red line that matters was correctly silent.
 *
 * So red is now reserved for the one model the provider's text tasks will
 * actually use. A merely PRESENT suspect model gets its family stated in
 * neutral type and stays enabled. Several assertions below exist specifically
 * to keep the chips out of the alarm palette.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { AISettings } from './AISettings';
import type { AISettings as AISettingsType } from '../types';
import en from '../public/locales/en.json';

// Resolve against the REAL shipped English copy, so a missing/renamed key
// fails here instead of shipping a raw `aiSettings.nonChatSelected` to users.
// The component's i18n instance is never initialized in tests.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, unknown>): string => {
      const template = key
        .split('.')
        .reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en);
      if (typeof template !== 'string') return key;
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(vars?.[name] ?? ''));
    },
  }),
}));

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  getPlatformStatus: vi.fn(() => new Promise(() => {})),
  getAIGovernance: vi.fn().mockResolvedValue({
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
    nous_enabled: true,
  }),
  GOVERNANCE_ALL_ALLOWED: {
    chat: true, transcription: true, translation: true,
    visual_analysis: true, caption: true, classification: true,
    summarization: true,
  },
}));
vi.mock('../services/aiLibraryService', () => ({
  aiLibraryService: { listAgents: vi.fn().mockResolvedValue([]) },
}));
vi.mock('./MCPServersPanel', () => ({ MCPServersPanel: () => null }));
vi.mock('./ApprovalsPanel', () => ({ ApprovalsPanel: () => null }));
vi.mock('./MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./AIHealthBoard', () => ({ AIHealthBoard: () => null }));

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

/** The exact configuration that broke production. */
const INCIDENT_MODEL = 'doubao-embedding-vision-251215';

function renderWithDoubao(models: string[], selected?: string) {
  return render(
    <AISettings
      settings={{
        ...baseSettings,
        providers: {
          doubao: {
            enabled: true,
            api_key_set: true,
            enabled_models: models,
            selected_model: selected ?? models[0],
          },
        },
      }}
      onSave={vi.fn()}
    />,
  );
}

describe('AISettings — BYOK non-chat model guard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('warns in red when the selected model is the embedding model from the incident', async () => {
    renderWithDoubao([INCIDENT_MODEL]);

    const warn = await screen.findByTestId('non-chat-warnline');
    // Names the model and what it looks like, and says what will break — a
    // bare "invalid model" would leave the user with no next step.
    expect(warn.textContent).toContain(INCIDENT_MODEL);
    expect(warn.textContent).toContain('an embedding model');
    expect(warn.textContent).toMatch(/summarization/i);
    // danger semantic token, not a raw hue class (frontend/index.css @theme).
    expect(warn.className).toContain('text-danger');
    // Announced, not just colored — red text alone reaches nobody using a
    // screen reader, and this is the one thing on the card they must act on.
    expect(warn).toHaveAttribute('role', 'alert');
  });

  it('says nothing when the selected model is an ordinary chat model', async () => {
    renderWithDoubao(['doubao-seed-2-0-pro-260215']);

    await waitFor(() => {
      expect(screen.getByText('doubao-seed-2-0-pro-260215')).toBeInTheDocument();
    });
    expect(screen.queryByTestId('non-chat-warnline')).toBeNull();
    expect(screen.queryByTestId('non-chat-kind-tag')).toBeNull();
  });

  it('names the family on a suspect chip, in neutral type', async () => {
    // Worth saying: the agent picker offers every enabled model, so this one
    // can be chosen there. Worth saying QUIETLY: having it enabled is normal.
    renderWithDoubao(['doubao-seed-2-0-pro-260215', INCIDENT_MODEL]);

    const tags = await screen.findAllByTestId('non-chat-kind-tag');
    expect(tags).toHaveLength(1);
    expect(tags[0].textContent).toBe('embedding');
    // The hint names the model and says, in as many words, that it still works.
    expect(tags[0].getAttribute('title')).toContain(INCIDENT_MODEL);
    // …and the headline warning is about what the tasks WILL use, and the
    // selected model here is fine.
    expect(screen.queryByTestId('non-chat-warnline')).toBeNull();
  });

  it('does not paint a working configuration red', async () => {
    // The reported case: three enabled models, only one of which chats — a
    // correct setup for a product that also does embeddings and images. The
    // chips must carry no part of the danger palette, and no alert icon.
    renderWithDoubao([
      'doubao-seed-2-0-lite-260428',
      'doubao-embedding-large-text-250515',
      'doubao-seedream-5-0-pro-260628',
    ]);

    const tags = await screen.findAllByTestId('non-chat-kind-tag');
    expect(tags.map((n) => n.textContent)).toEqual(['embedding', 'image']);
    for (const tag of tags) {
      // Walk up to the chip and assert the whole thing is off the alarm palette.
      const chip = tag.parentElement as HTMLElement;
      expect(chip.className).not.toMatch(/danger/);
      expect(chip.querySelector('svg.lucide-triangle-alert')).toBeNull();
    }
    // The selected model is the chat one, so nothing is actually wrong.
    expect(screen.queryByTestId('non-chat-warnline')).toBeNull();
  });

  it('does not block: the suspect model stays enabled and removable', async () => {
    renderWithDoubao([INCIDENT_MODEL]);

    const tag = await screen.findByTestId('non-chat-kind-tag');
    expect((tag.parentElement as HTMLElement).textContent).toContain(INCIDENT_MODEL);
    // The escape hatch is the same X as any other chip.
    fireEvent.click(screen.getByLabelText(`Remove ${INCIDENT_MODEL}`));
    await waitFor(() => {
      expect(screen.queryByTestId('non-chat-warnline')).toBeNull();
    });
  });

  it('falls back to the first enabled model for legacy rows with no selected_model', async () => {
    renderWithDoubao([INCIDENT_MODEL], '');

    const warn = await screen.findByTestId('non-chat-warnline');
    expect(warn.textContent).toContain(INCIDENT_MODEL);
  });
});
