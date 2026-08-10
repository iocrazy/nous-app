import { render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AgentIdentityHeader } from './AgentIdentityHeader';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => (typeof fallback === 'string' ? fallback : key),
  }),
}));

describe('AgentIdentityHeader', () => {
  it('shows the agent name and description when an agent is chosen', () => {
    const { getByTestId } = render(
      <AgentIdentityHeader name="Script AI" description="Outlines and expands scenes" />,
    );
    const root = getByTestId('agent-identity');
    expect(root.textContent).toContain('Script AI');
    expect(root.textContent).toContain('Outlines and expands scenes');
  });

  it('falls back to a generic description when the agent has none', () => {
    const { getByTestId } = render(<AgentIdentityHeader name="Script AI" />);
    expect(getByTestId('agent-identity').textContent).toContain('No description');
  });

  it('shows a placeholder and hint when no agent is chosen', () => {
    const { getByTestId } = render(<AgentIdentityHeader name={null} />);
    const root = getByTestId('agent-identity');
    expect(root.textContent).toContain('Choose an agent');
    expect(root.textContent).toContain('Pick who you want to work with');
  });

  it('renders an avatar icon in both the chosen and unchosen states (icon fallback)', () => {
    const { getByTestId, rerender } = render(<AgentIdentityHeader name={null} />);
    expect(getByTestId('agent-identity').querySelector('svg')).not.toBeNull();

    rerender(<AgentIdentityHeader name="Script AI" icon="does-not-exist-slug" />);
    expect(getByTestId('agent-identity').querySelector('svg')).not.toBeNull();
  });

  it('styles the avatar with agent tone only once an agent is chosen', () => {
    const { getByTestId, rerender } = render(<AgentIdentityHeader name={null} />);
    const avatar = () => getByTestId('agent-identity').querySelector('span')!;
    expect(avatar().className).toContain('border-ink-700');
    expect(avatar().className).not.toContain('border-agent-line');

    rerender(<AgentIdentityHeader name="Script AI" />);
    expect(avatar().className).toContain('border-agent-line');
  });

  it('renders the action slot inline with identity', () => {
    const { getByTestId } = render(
      <AgentIdentityHeader
        name="Script AI"
        action={<button data-testid="agent-switch">Switch</button>}
      />,
    );
    expect(getByTestId('agent-switch')).toBeTruthy();
  });
});
