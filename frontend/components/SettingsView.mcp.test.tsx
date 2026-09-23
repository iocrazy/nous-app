/**
 * MCP moved out of Settings → AI sub tabs into its own left-nav entry
 * (2026-09). SettingsView owns rendering it for the `mcp` tab.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../services/apiKeyService', () => ({
  listApiKeys: vi.fn().mockResolvedValue([]),
  getAvailableScopes: vi.fn().mockResolvedValue([]),
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
vi.mock('../hooks/useWorkspaceScope', () => ({
  useWorkspaceScope: () => ({ effectiveTeamId: null }),
}));
vi.mock('./workflow/WorkflowTemplateEditor', () => ({ WorkflowTemplateEditor: () => null }));
vi.mock('./MCPServersPanel', () => ({
  MCPServersPanel: () => <div data-testid="mcp-panel-stub" />,
}));

import { SettingsView } from './SettingsView';

describe('SettingsView MCP tab', () => {
  it('renders the MCP servers panel as a top-level tab', () => {
    render(<SettingsView settings={{} as never} onUpdateSettings={vi.fn()} activeTab="mcp" embedded />);
    expect(screen.getByTestId('mcp-panel-stub')).toBeInTheDocument();
  });
});
