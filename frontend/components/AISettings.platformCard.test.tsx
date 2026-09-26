/**
 * Tests for the "Nous (Platform)" models card.
 *
 * The card body is the same "Enabled Models" chips + "Add Model" picker as
 * every BYOK card (2026-09-25). Under test:
 *   1. It lists ALL enabled platform model types (llm / embedding / asr /
 *      image), not just llm — one chip per row, each with a neutral TYPE tag.
 *   2. Each chip reads as the admin identifier (`actual_model`, else the row
 *      `name`) — the same string the admin AI Models card shows. The
 *      migration-authored `display_name` never appears on the card.
 *   3. The admin-internal `description` note (which used to leak private IPs /
 *      BYOK refs) never renders.
 *   4. A row authorized on nous-engine but not loaded is still a chip, tagged
 *      "(not loaded)" and titled with why.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import { AISettings } from './AISettings';
import {
  ENGINE_DOWN,
  baseAISettings,
  withPlatform,
  type PlatformRowSpec,
} from '../tests/fixtures/platform';

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

// Chip text is `actual_model`, else the row `name` (moss-asr has no
// actual_model, like the jimeng-local rows in production). The platform list
// rides on the settings (spec 2026-09-25) — the wire shape is
// tests/fixtures/platform. `display_name` is not even on the wire any more;
// the old names are kept here only to prove none of them reach the page.
const FOUR_TYPES: PlatformRowSpec[] = [
  {
    name: 'nous-doubao-embedding-vision',
    actual_model: 'doubao-embedding-vision-251215',
    type: 'embedding',
    pricing_type: 'per_request',
    pricing_value: 1,
  },
  { name: 'moss-asr', actual_model: '', type: 'asr', pricing_type: 'per_hour', pricing_value: 3 },
  { name: 'nous-llm', actual_model: 'qwen3-8b-instruct', type: 'llm', pricing_type: 'per_token', pricing_value: 2 },
  {
    name: 'nous-seedream-3',
    actual_model: 'doubao-seedream-3-0-t2i-250415',
    type: 'image',
    pricing_type: 'per_request',
    pricing_value: 5,
  },
];
const DISPLAY_NAMES = ['Doubao Embedding Vision', 'MOSS ASR', 'Nous LLM', 'Doubao Seedream 3.0'];

vi.mock('../services/aiService', () => ({
  saveAISettings: vi.fn(async (s: unknown) => s), // PUT echoes the saved settings
  testAIConnection: vi.fn(),
  // Never answers: the card falls back to the status the settings carried.
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

function renderSettings(rows: PlatformRowSpec[] = FOUR_TYPES, engine = undefined as undefined | typeof ENGINE_DOWN) {
  const settings = withPlatform(baseAISettings(), rows, engine ? { engine } : {});
  return render(<AISettings settings={settings} onSave={vi.fn()} />);
}

/** The platform card (header + body). */
function platformCard(): HTMLElement {
  const card = screen.getByText('Nous (Platform)').closest('.rounded-xl');
  if (!card) throw new Error('platform card not found');
  return card as HTMLElement;
}

/** The chip for one platform row, keyed by the row `name`. */
function chip(name: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(
    `[data-testid="platform-model-row"][data-model-name="${name}"]`,
  );
}

describe('AISettings — Nous (Platform) models card', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders ALL enabled model types (not just llm) as chips, each with a type tag', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    // Every configured model — regardless of type — is a chip whose text is
    // the admin identifier, followed by its type tag.
    const expected: Array<[string, string, string]> = [
      ['nous-llm', 'qwen3-8b-instruct', 'LLM'],
      ['moss-asr', 'moss-asr', 'ASR'],
      ['nous-doubao-embedding-vision', 'doubao-embedding-vision-251215', 'EMBEDDING'],
      ['nous-seedream-3', 'doubao-seedream-3-0-t2i-250415', 'IMAGE'],
    ];
    for (const [name, label, type] of expected) {
      const c = chip(name);
      expect(c, name).not.toBeNull();
      expect(within(c!).getByText(label)).toBeInTheDocument();
      const tag = within(c!).getByTestId('non-chat-kind-tag');
      expect(tag).toHaveTextContent(type);
      expect(tag.textContent).toBe(type);
      expect(within(c!).getByRole('button', { name: `Remove ${label}` })).toBeInTheDocument();
    }
  });

  it('sorts chips llm → asr → embedding → image', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    const order = screen
      .getAllByTestId('platform-model-row')
      .map((el) => el.getAttribute('data-model-name'));
    expect(order).toEqual(['nous-llm', 'moss-asr', 'nous-doubao-embedding-vision', 'nous-seedream-3']);
  });

  it('never shows a display_name anywhere on the card — only the admin identifiers', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    const card = platformCard();
    for (const display of DISPLAY_NAMES) {
      expect(card.textContent).not.toContain(display);
    }
    // Nor in the Add Model picker (every row is enabled, so it is empty) —
    // and nowhere else on the page either (task pickers use the same rule).
    for (const display of DISPLAY_NAMES) {
      expect(document.body.textContent).not.toContain(display);
    }
  });

  it('keeps a not-loaded nous-engine row as a chip tagged "(not loaded)" and titled with why', async () => {
    renderSettings([
      FOUR_TYPES[2],
      { name: 'nous-qwen3-27b', actual_model: 'qwen3-27b', type: 'llm', status: 'idle' },
    ]);

    await waitFor(() => expect(chip('nous-qwen3-27b')).not.toBeNull());
    const idle = chip('nous-qwen3-27b')!;
    expect(within(idle).getByText('qwen3-27b')).toBeInTheDocument();
    const tag = within(idle).getByTestId('non-chat-kind-tag');
    expect(tag.textContent).toBe('LLM (not loaded)');
    expect(tag).toHaveAttribute('title', 'Not loaded on nous-engine');
    // The loaded row carries the plain type tag.
    const loadedTag = within(chip('nous-llm')!).getByTestId('non-chat-kind-tag');
    expect(loadedTag.textContent).toBe('LLM');
    expect(loadedTag).toHaveAttribute('title', 'LLM');
  });

  it('shows the empty-state copy when no platform models are configured', async () => {
    renderSettings([]);

    await waitFor(() => {
      expect(screen.getByText('No platform models available.')).toBeInTheDocument();
    });
    expect(screen.queryAllByTestId('platform-model-row')).toHaveLength(0);
  });

  it('says so on the card when nous-engine cannot be reached — and keeps the list', async () => {
    renderSettings(FOUR_TYPES, ENGINE_DOWN);

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    const notice = within(platformCard()).getByTestId('platform-engine-unreachable');
    expect(notice).toHaveTextContent('nous-engine is unreachable right now');
    expect(screen.getAllByTestId('platform-model-row')).toHaveLength(4);
  });

  it('shows no unreachable notice while the engine answers', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    expect(screen.queryByTestId('platform-engine-unreachable')).toBeNull();
  });

  it('renders no API key box and no Test Connection on the managed card', async () => {
    renderSettings();

    await waitFor(() => expect(chip('nous-llm')).not.toBeNull());
    const card = platformCard();
    expect(within(card).queryByPlaceholderText(/key/i)).toBeNull();
    expect(within(card).queryByRole('button', { name: /test connection/i })).toBeNull();
  });
});
