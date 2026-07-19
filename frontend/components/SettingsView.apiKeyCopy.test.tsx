/**
 * Part 1 — Settings → API Management: the prefix cell copies the FULL key.
 *
 * Keys are encrypted at rest (#1005); the table only ever shows the masked
 * prefix. Clicking the chip or its copy button now calls the owner-only reveal
 * endpoint and copies the COMPLETE key to the clipboard — the full key is never
 * rendered into the DOM (copy-only). This asserts reveal → clipboard(full key)
 * and the copied-state feedback.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

// ── mocks (hoisted — inline literals only) ────────────────────────────────────
const KEY_ROW = {
  id: 42,
  key_id: 'kid-42',
  key_prefix: 'dk_abcd1234...',
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

const FULL_KEY = 'dk_abcd1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab';

vi.mock('../services/apiKeyService', () => ({
  listApiKeys: vi.fn().mockResolvedValue([
    {
      id: 42,
      key_id: 'kid-42',
      key_prefix: 'dk_abcd1234...',
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
  revealApiKey: vi
    .fn()
    .mockResolvedValue(
      'dk_abcd1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab',
    ),
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

import { SettingsView } from './SettingsView';
import * as apiKeyService from '../services/apiKeyService';

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

describe('SettingsView — API key full-key copy (reveal)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
  });

  it('reveals and copies the FULL key when the copy button is clicked', async () => {
    renderApiTab();
    await screen.findByText(KEY_ROW.key_prefix);

    fireEvent.click(screen.getByLabelText('Copy full key'));

    await waitFor(() =>
      expect(apiKeyService.revealApiKey).toHaveBeenCalledWith('kid-42'),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(FULL_KEY));
    // Never copies the masked prefix.
    expect(writeText).not.toHaveBeenCalledWith(KEY_ROW.key_prefix);
    await waitFor(() => expect(screen.getByLabelText('Copied')).toBeTruthy());
  });

  it('reveals and copies the FULL key when the chip itself is clicked', async () => {
    renderApiTab();
    const chip = await screen.findByText(KEY_ROW.key_prefix);

    fireEvent.click(chip);

    await waitFor(() =>
      expect(apiKeyService.revealApiKey).toHaveBeenCalledWith('kid-42'),
    );
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(FULL_KEY));
  });
});
