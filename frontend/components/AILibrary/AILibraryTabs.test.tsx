/**
 * AILibraryTabs — the strip shared by the agents and skills galleries.
 *   1. The active tab is inert; the other one navigates. The skills gallery
 *      had no strip at all, so there was no way back to the agents roster.
 *   2. Marketplace stays inert wherever it is rendered.
 *   3. Counts are optional — the skills page does not load agents.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { AILibraryTabs } from './AILibraryTabs';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

function renderAt(path: string, ui: React.ReactElement) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/team/:teamId/*" element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('AILibraryTabs', () => {
  it('sends the skills tab back to the agents gallery index', () => {
    navigate.mockClear();
    renderAt('/team/7/ai-library/skills', <AILibraryTabs active="skills" />);
    fireEvent.click(screen.getByRole('button', { name: /Agents/ }));
    expect(navigate).toHaveBeenCalledWith('/team/7/ai-library');
  });

  it('sends the agents tab to the skills gallery', () => {
    navigate.mockClear();
    renderAt('/team/7/ai-library', <AILibraryTabs active="agents" />);
    fireEvent.click(screen.getByRole('button', { name: /Skills/ }));
    expect(navigate).toHaveBeenCalledWith('/team/7/ai-library/skills');
  });

  it('renders the active tab as inert text, not a button', () => {
    renderAt('/team/7/ai-library', <AILibraryTabs active="agents" />);
    expect(screen.queryByRole('button', { name: /Agents/ })).toBeNull();
    expect(screen.getByRole('button', { name: /Skills/ })).toBeTruthy();
  });

  it('never makes Marketplace clickable', () => {
    renderAt('/team/7/ai-library', <AILibraryTabs active="agents" />);
    expect(screen.queryByRole('button', { name: /Marketplace/ })).toBeNull();
    expect(screen.getByText(/Marketplace/)).toBeTruthy();
  });

  it('shows counts when given and omits them when not', () => {
    const { unmount } = renderAt(
      '/team/7/ai-library',
      <AILibraryTabs active="agents" agentCount={12} skillCount={5} />,
    );
    expect(screen.getByText('12')).toBeTruthy();
    expect(screen.getByText('5')).toBeTruthy();
    unmount();

    renderAt('/team/7/ai-library/skills', <AILibraryTabs active="skills" skillCount={5} />);
    // The skills page does not load agents, so that tab carries no number.
    expect(screen.getByRole('button', { name: 'Agents' })).toBeTruthy();
  });

  it('renders the actions slot for the page primary button', () => {
    renderAt(
      '/team/7/ai-library',
      <AILibraryTabs active="agents" actions={<button type="button">New Agent</button>} />,
    );
    expect(screen.getByRole('button', { name: 'New Agent' })).toBeTruthy();
  });
});
