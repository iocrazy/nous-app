import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { UserSettings } from '../types';
import { SettingsModal } from './SettingsModal';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('../services/cookiesService', () => ({
  fetchCookieStatuses: vi.fn().mockResolvedValue([]),
}));
vi.mock('./PersonalSettings', () => ({ PersonalSettings: () => <div data-testid="personal-stub" /> }));
vi.mock('./TeamSettings', () => ({ TeamSettings: () => null }));
vi.mock('./VersionBadge', () => ({ VersionBadge: () => null }));
// Stand-in for SettingsView that exposes which tab it was asked to render.
vi.mock('./SettingsView', () => ({
  SettingsView: ({ activeTab }: { activeTab: string }) => (
    <div data-testid="settings-view-stub">{activeTab}</div>
  ),
}));

const user = { id: 'u1', name: 'Test User', email: 'test@example.com' };

describe('SettingsModal navigation', () => {
  it('lists MCP as its own left nav item right after AI and routes it to SettingsView', () => {
    render(
      <SettingsModal
        isOpen
        onClose={() => {}}
        user={user}
        settings={{} as UserSettings}
        onUpdateSettings={() => {}}
      />,
    );
    const navButtons = screen.getAllByRole('button', { name: 'settings.nav.mcp' });
    expect(navButtons.length).toBeGreaterThan(0);
    const labels = screen
      .getAllByRole('button')
      .map((b) => b.textContent)
      .filter((l): l is string => !!l && l.startsWith('settings.nav.'));
    const aiIdx = labels.indexOf('settings.nav.ai');
    expect(labels[aiIdx + 1]).toBe('settings.nav.mcp');

    fireEvent.click(navButtons[navButtons.length - 1]);
    expect(screen.getByTestId('settings-view-stub')).toHaveTextContent('mcp');
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('settings.nav.mcp');
  });
});
