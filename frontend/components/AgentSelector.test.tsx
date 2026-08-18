/**
 * AgentSelector dropdown must stay usable with a long roster.
 *
 * Bug (2026-08-17): the open dropdown had no max-height and no scroll while
 * its host (FloatingChatWidget) is overflow-hidden — with 19 agents the list
 * grew ~900px tall and everything below the widget's bottom edge was
 * clipped and unreachable (user report: "无法选择下面的 agent").
 *
 * jsdom does no layout, so the pin here is the class contract: the list
 * container must carry BOTH a max-height cap and overflow-y-auto. Dropping
 * either one re-opens the clipping bug.
 */
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AgentSelector, type AgentOption } from './AgentSelector';

const roster: AgentOption[] = Array.from({ length: 19 }, (_, i) => ({
  id: `agent-${i}`,
  name: `Agent ${i}`,
  description: `Test agent number ${i}`,
}));

afterEach(() => cleanup());

function openDropdown() {
  render(
    <AgentSelector agents={roster} selectedId="agent-0" onSelect={vi.fn()} />,
  );
  fireEvent.click(screen.getByRole('button', { name: /Agent 0/ }));
}

describe('AgentSelector dropdown', () => {
  it('renders every agent option even with a long roster', () => {
    openDropdown();
    // All 19 stay in the DOM — scrolling, not truncation, handles overflow.
    expect(screen.getByText('Agent 18')).toBeInTheDocument();
  });

  it('caps the list height and scrolls instead of clipping', () => {
    openDropdown();
    const list = screen.getByText('Agent 18').closest('div[class*="absolute"]');
    expect(list).not.toBeNull();
    const cls = (list as HTMLElement).className;
    expect(cls).toMatch(/overflow-y-auto/);
    expect(cls).toMatch(/max-h-/);
    // The old clipping class must be gone from the scroll container.
    expect(cls).not.toMatch(/(^|\s)overflow-hidden(\s|$)/);
  });
});
