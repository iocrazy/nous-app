/**
 * A1 —— 「等我的」横条。
 *
 * 关键约束：**空态整条零渲染**。一条永远在那儿的空横条会训练用户忽略它，
 * 而它存在的全部意义就是「出现 == 有事卡在你手上」。
 */

import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AttentionStrip, loadAttentionCollapsed, saveAttentionCollapsed } from './AttentionStrip';
import type { AttentionItem } from './attentionItems';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, fallback?: string) => fallback ?? key }),
}));

afterEach(cleanup);

const ITEMS: AttentionItem[] = [
  { type: 'question', id: 'question:1', title: 'Confirm schedule', detail: 'Monday or Wednesday?', issueId: 1 },
  { type: 'approval', id: 'ap-1', title: 'pre_tool_use', detail: 'Wants to publish live', },
  { type: 'review', id: 'review:3', title: 'Trailer cut v2', detail: 'NOUS-7', issueId: 3 },
];

function renderStrip(props: Partial<React.ComponentProps<typeof AttentionStrip>> = {}) {
  return render(
    <MemoryRouter>
      <AttentionStrip
        items={ITEMS}
        collapsed={false}
        onToggle={vi.fn()}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        teamId="8"
        issueLinkFor={() => null}
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('AttentionStrip', () => {
  it('renders nothing at all when nothing is waiting', () => {
    const { container } = renderStrip({ items: [] });
    expect(container.firstChild).toBeNull();
  });

  it('shows the count badge and one card per item', () => {
    const { container } = renderStrip();
    const strip = container.querySelector('[data-testid="attention-strip"]') as HTMLElement;
    expect(strip).not.toBeNull();
    expect(strip.textContent).toMatch(/Waiting on you/);
    expect(strip.textContent).toMatch(/3/);
    expect(container.querySelectorAll('[data-testid="attention-card"]')).toHaveLength(3);
  });

  it('hides the cards but keeps the header when collapsed', () => {
    const { container } = renderStrip({ collapsed: true });
    expect(container.querySelector('[data-testid="attention-strip"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-testid="attention-card"]')).toHaveLength(0);
  });

  it('reports a collapse toggle to the parent', () => {
    const onToggle = vi.fn();
    const { container } = renderStrip({ onToggle });
    fireEvent.click(container.querySelector('[data-testid="attention-toggle"]') as HTMLElement);
    expect(onToggle).toHaveBeenCalledWith(true);
  });

  it('wires Approve and Reject on an approval card', () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    renderStrip({ onApprove, onReject });
    fireEvent.click(screen.getByRole('button', { name: /^approve$/i }));
    expect(onApprove).toHaveBeenCalledWith('ap-1');
    fireEvent.click(screen.getByRole('button', { name: /^reject$/i }));
    expect(onReject).toHaveBeenCalledWith('ap-1');
  });

  it('links a question card to its issue when the identifier is resolvable', () => {
    const { container } = renderStrip({ issueLinkFor: (id: number) => `/team/8/todolist/NOUS-${id}` });
    const link = container.querySelector('a[href="/team/8/todolist/NOUS-1"]');
    expect(link).not.toBeNull();
  });

  it('degrades a question card to a non-link when the issue cannot be resolved', () => {
    const { container } = renderStrip({ items: [ITEMS[0]], issueLinkFor: () => null });
    expect(container.querySelector('a')).toBeNull();
    expect(container.querySelector('[data-testid="attention-card"]')).not.toBeNull();
  });
});

describe('AttentionStrip collapse persistence', () => {
  it('round-trips through localStorage and defaults to expanded', () => {
    expect(loadAttentionCollapsed('team:8')).toBe(false);
    saveAttentionCollapsed('team:8', true);
    expect(loadAttentionCollapsed('team:8')).toBe(true);
  });
});

describe('AttentionStrip — queued card (phase 2a §4)', () => {
  it('renders a queued item with its count as the detail', () => {
    renderStrip({ items: [{ type: 'queued', id: 'queued:5', title: 'Paused with mail', detail: '2 queued', issueId: 5 }] });
    expect(screen.getByText('Paused with mail')).toBeTruthy();
    expect(screen.getByText('2 queued')).toBeTruthy();
  });
});
