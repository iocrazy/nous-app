/**
 * Settings → API Management: the new-key expiration field is the shared
 * DateTimePopover in `mode="single"`, not a native `<input type="date">`.
 *
 * The pin that matters is the WINDOW. The native input carried
 * `min={today}`; dropping that on the way to a popover would have quietly
 * allowed minting a key that is already expired. `minAt` reproduces it by
 * rendering every earlier day `disabled` — asserted below by clicking
 * yesterday and demanding nothing happens, not merely by reading an
 * attribute. The "Expires in N days" read-out must keep working off the same
 * `keyForm.expirationDate` string it always read.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../services/apiKeyService', () => ({
  listApiKeys: vi.fn().mockResolvedValue([]),
  getAvailableScopes: vi.fn().mockResolvedValue([]),
  revealApiKey: vi.fn(),
  createApiKey: vi.fn(),
  updateApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

vi.mock('../contexts/ThemeContext', () => ({
  useTheme: () => ({ preference: 'dark', resolved: 'dark', setPreference: vi.fn() }),
}));

vi.mock('./ConfirmDialog', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }));

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));

vi.mock('./AISettings', () => ({ default: () => null }));
vi.mock('./LogsPanel', () => ({ LogsPanel: () => null }));
vi.mock('./SystemMonitorPanel', () => ({ SystemMonitorPanel: () => null }));
vi.mock('./TaskCenter/TaskCenter', () => ({ TaskCenter: () => null }));
vi.mock('./TagsSettings', () => ({ TagsSettings: () => null }));
vi.mock('./ApiDocsPanel', () => ({ ApiDocsPanel: () => null }));
vi.mock('./CookiesSettings', () => ({ CookiesSettings: () => null }));
vi.mock('./ChatTempTtlPanel', () => ({ ChatTempTtlPanel: () => null }));
vi.mock('../hooks/useWorkspaceScope', () => ({
  useWorkspaceScope: () => ({ effectiveTeamId: null }),
}));
vi.mock('./workflow/WorkflowTemplateEditor', () => ({ WorkflowTemplateEditor: () => null }));

import { SettingsView } from './SettingsView';

async function openExpirationPicker() {
  render(<SettingsView settings={{} as never} onUpdateSettings={vi.fn()} activeTab="api" />);
  fireEvent.click(await screen.findByText('Create API Key'));
  fireEvent.click(await screen.findByText('Select Date'));
  const trigger = screen.getByTestId('api-key-expiration-trigger');
  fireEvent.click(trigger);
  return trigger;
}

describe('SettingsView — API key expiration uses DateTimePopover', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(new Date(2026, 7, 13, 10, 0, 0)); // local 2026-08-13
  });

  afterEach(() => vi.useRealTimers());

  it('the trigger opens the popover and a picked day fills the field', async () => {
    const trigger = await openExpirationPicker();
    expect(screen.getByTestId('date-time-popover')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('2026-08-20'));

    expect(trigger.textContent).toContain('2026-08-20');
    // The "Expires in N days" read-out still derives from the same string.
    expect(screen.getByText(/Expires in 7 days/)).toBeTruthy();
  });

  it('minAt floors the window at today — earlier days are unselectable', async () => {
    const trigger = await openExpirationPicker();

    const yesterday = screen.getByLabelText('2026-08-12');
    expect(yesterday).toBeDisabled();
    fireEvent.click(yesterday);
    expect(trigger.textContent).not.toContain('2026-08-12');

    // Today itself is inside the window (the old native `min` was inclusive).
    expect(screen.getByLabelText('2026-08-13')).not.toBeDisabled();
  });

  it('Clear empties the field', async () => {
    const trigger = await openExpirationPicker();
    fireEvent.click(screen.getByLabelText('2026-08-20'));
    expect(trigger.textContent).toContain('2026-08-20');

    fireEvent.click(screen.getByTestId('api-key-expiration-trigger'));
    fireEvent.click(screen.getByTestId('date-time-clear'));

    expect(trigger.textContent).not.toContain('2026-08-20');
    expect(screen.queryByText(/Expires in/)).toBeNull();
  });
});
