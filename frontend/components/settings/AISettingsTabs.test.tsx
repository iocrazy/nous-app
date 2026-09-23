import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { AISettings } from '../../types';
import { AISettingsTabs } from './AISettingsTabs';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('../AISettings', () => ({ default: () => <div data-testid="ai-settings-stub" /> }));
vi.mock('../AgentMemoriesPanel', () => ({ AgentMemoriesPanel: () => null }));
vi.mock('../MemoryPanel', () => ({ MemoryPanel: () => null }));
vi.mock('./LocalCliSettings', () => ({ LocalCliSettings: () => null }));
vi.mock('./VectorsPanel', () => ({ VectorsPanel: () => <div data-testid="vectors-panel-stub" /> }));

describe('AISettingsTabs', () => {
  it('has a Vectors sub tab and no MCP sub tab', () => {
    render(<AISettingsTabs settings={{} as AISettings} onSave={() => {}} />);
    expect(screen.getByTestId('ai-subtab-vectors')).toHaveTextContent('settings.aiTabs.vectors');
    expect(screen.queryByTestId('ai-subtab-mcp')).not.toBeInTheDocument();
  });

  it('renders the Vectors panel when the Vectors sub tab is clicked', () => {
    render(<AISettingsTabs settings={{} as AISettings} onSave={() => {}} />);
    expect(screen.queryByTestId('vectors-panel-stub')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId('ai-subtab-vectors'));
    expect(screen.getByTestId('vectors-panel-stub')).toBeInTheDocument();
  });
});
