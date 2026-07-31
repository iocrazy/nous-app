/**
 * WorkspaceTopBar — Autopilot chip (M4 Autopilot task O1/O2/O3).
 *
 * The chip is the project-level `autopilot_enabled` master switch: shown
 * only when the host has wired both `projectId` and `autopilotEnabled`
 * through, toggled with an optimistic local flip that reverts if the PATCH
 * fails (this is the topbar's first mutation — no prior topbar pattern to
 * match, so it mirrors AgentMemoriesPanel's capture-before-mutate /
 * revert-on-failure idiom instead).
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../../public/locales/en.json';
import { WorkspaceTopBar } from './WorkspaceTopBar';
import { ToastProvider } from '../Toast';
import type { Project } from '../../types';

function makeI18n(): I18n {
  const instance = createInstance();
  instance.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
  });
  return instance;
}

const mockProjectsService = vi.hoisted(() => ({
  updateProject: vi.fn(),
}));
vi.mock('../../services/projectsService', () => mockProjectsService);

function renderTopBar(
  overrides: {
    projectId?: string;
    autopilotEnabled?: boolean;
    onAutopilotChange?: (enabled: boolean) => void;
    canWrite?: boolean;
  } = {},
) {
  const onAutopilotChange = overrides.onAutopilotChange ?? vi.fn();
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <ToastProvider>
        <WorkspaceTopBar
          projectName="Spring Campaign"
          onBack={vi.fn()}
          canWrite={overrides.canWrite ?? true}
          projectId={overrides.projectId ?? '10'}
          autopilotEnabled={overrides.autopilotEnabled}
          onAutopilotChange={onAutopilotChange}
        />
      </ToastProvider>
    </I18nextProvider>,
  );
  return { ...utils, onAutopilotChange };
}

beforeEach(() => {
  mockProjectsService.updateProject.mockReset();
});

describe('WorkspaceTopBar — Autopilot chip', () => {
  it('renders nothing when autopilotEnabled is not provided (host has not wired it through)', () => {
    render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceTopBar projectName="Spring Campaign" onBack={vi.fn()} projectId="10" />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('workspace-autopilot-chip')).toBeNull();
  });

  it('renders nothing when projectId is not provided', () => {
    render(
      <I18nextProvider i18n={makeI18n()}>
        <WorkspaceTopBar projectName="Spring Campaign" onBack={vi.fn()} autopilotEnabled />
      </I18nextProvider>,
    );
    expect(screen.queryByTestId('workspace-autopilot-chip')).toBeNull();
  });

  it('renders the "on" state when autopilotEnabled is true', () => {
    renderTopBar({ autopilotEnabled: true });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'on');
  });

  it('renders the "off" state when autopilotEnabled is false', () => {
    renderTopBar({ autopilotEnabled: false });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'off');
  });

  it('optimistically flips on click and PATCHes autopilot_enabled', async () => {
    mockProjectsService.updateProject.mockResolvedValue({ autopilot_enabled: false } as Project);
    const onAutopilotChange = vi.fn();
    renderTopBar({ autopilotEnabled: true, onAutopilotChange });

    const chip = screen.getByTestId('workspace-autopilot-chip');
    fireEvent.click(chip);

    // Flips immediately, before the PATCH resolves.
    expect(chip).toHaveAttribute('data-autopilot', 'off');
    expect(mockProjectsService.updateProject).toHaveBeenCalledWith('10', { autopilot_enabled: false });

    await waitFor(() => expect(onAutopilotChange).toHaveBeenCalledWith(false));
  });

  it('reverts the optimistic flip when the PATCH fails', async () => {
    mockProjectsService.updateProject.mockRejectedValue(new Error('network down'));
    renderTopBar({ autopilotEnabled: true });

    const chip = screen.getByTestId('workspace-autopilot-chip');
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('data-autopilot', 'off');

    await waitFor(() => expect(chip).toHaveAttribute('data-autopilot', 'on'));
  });

  it('is disabled when canWrite is false', () => {
    renderTopBar({ autopilotEnabled: true, canWrite: false });
    expect(screen.getByTestId('workspace-autopilot-chip')).toBeDisabled();
  });

  it('re-syncs from a fresh autopilotEnabled prop (e.g. navigating to a different project)', () => {
    const { rerender } = renderTopBar({ autopilotEnabled: true });
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'on');

    rerender(
      <I18nextProvider i18n={makeI18n()}>
        <ToastProvider>
          <WorkspaceTopBar
            projectName="Another Project"
            onBack={vi.fn()}
            canWrite
            projectId="99"
            autopilotEnabled={false}
            onAutopilotChange={vi.fn()}
          />
        </ToastProvider>
      </I18nextProvider>,
    );
    expect(screen.getByTestId('workspace-autopilot-chip')).toHaveAttribute('data-autopilot', 'off');
  });
});
