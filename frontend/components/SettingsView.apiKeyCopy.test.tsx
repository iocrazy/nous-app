/**
 * Part 1 — Settings → API Management: KEY PREFIX cell is click-to-copy.
 *
 * The full key is only shown once at creation (encrypt-at-rest, #1005); the
 * table keeps only the prefix. This asserts the prefix chip + its copy button
 * copy `key.key_prefix` to the clipboard and surface the copied state, reusing
 * the file's existing handleCopyKey / copiedKeyId feedback.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// ── mocks (hoisted — inline literals only) ────────────────────────────────────
const KEY_ROW = {
  id: 42,
  key_id: 'kid-42',
  key_prefix: 'mhk_abcd1234',
  key_value: null,
  key_value_set: true,
  name: 'CI Token',
  description: 'used by CI',
  scopes: ['videos:read'],
  status: 'active',
  expires_at: null,
  last_used_at: null,
  usage_count: 3,
  rate_limit: null,
  created_at: '2026-07-01T10:00:00Z',
  updated_at: '2026-07-01T10:00:00Z',
};

vi.mock('../services/apiKeyService', () => ({
  listApiKeys: vi.fn().mockResolvedValue([
    {
      id: 42,
      key_id: 'kid-42',
      key_prefix: 'mhk_abcd1234',
      key_value: null,
      key_value_set: true,
      name: 'CI Token',
      description: 'used by CI',
      scopes: ['videos:read'],
      status: 'active',
      expires_at: null,
      last_used_at: null,
      usage_count: 3,
      rate_limit: null,
      created_at: '2026-07-01T10:00:00Z',
      updated_at: '2026-07-01T10:00:00Z',
    },
  ]),
  getAvailableScopes: vi.fn().mockResolvedValue([]),
  createApiKey: vi.fn(),
  updateApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

vi.mock('../contexts/ThemeContext', () => ({
  useTheme: () => ({ preference: 'dark', resolved: 'dark', setPreference: vi.fn() }),
}));

vi.mock('./ConfirmDialog', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));

// Heavy tab panels not exercised by the API tab.
vi.mock('./AISettings', () => ({ default: () => null }));
vi.mock('./LogsPanel', () => ({ LogsPanel: () => null }));
vi.mock('./SystemMonitorPanel', () => ({ SystemMonitorPanel: () => null }));
vi.mock('./TaskCenter/TaskCenter', () => ({ TaskCenter: () => null }));
vi.mock('./TagsSettings', () => ({ TagsSettings: () => null }));
vi.mock('./ApiDocsPanel', () => ({ ApiDocsPanel: () => null }));
vi.mock('./CookiesSettings', () => ({ CookiesSettings: () => null }));
vi.mock('./ChatTempTtlPanel', () => ({ ChatTempTtlPanel: () => null }));
// Inspiration tokens section embedded below API keys — not under test here.
vi.mock('./Inspiration/ApiTokensPanel', () => ({ ApiTokensPanel: () => null }));

import { SettingsView } from './SettingsView';

const writeText = vi.fn().mockResolvedValue(undefined);

function renderApiTab() {
  return render(
    <SettingsView
      settings={{} as never}
      onUpdateSettings={vi.fn()}
      activeTab="api"
    />,
  );
}

describe('SettingsView — API key prefix click-to-copy', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('copies the prefix and shows copied state when the copy button is clicked', async () => {
    renderApiTab();
    await screen.findByText(KEY_ROW.key_prefix);

    fireEvent.click(screen.getByLabelText('Copy key prefix'));

    expect(writeText).toHaveBeenCalledWith(KEY_ROW.key_prefix);
    await waitFor(() => expect(screen.getByLabelText('Copied')).toBeTruthy());
  });

  it('copies the prefix when the chip itself is clicked', async () => {
    renderApiTab();
    const chip = await screen.findByText(KEY_ROW.key_prefix);

    fireEvent.click(chip);

    expect(writeText).toHaveBeenCalledWith(KEY_ROW.key_prefix);
  });
});
