/**
 * MentionDropdown.test.tsx — RTL tests for the @mention candidate list.
 *
 * MentionDropdown has no i18n dependency — no react-i18next mock needed.
 *
 * The component uses onMouseDown (not onClick) to avoid blurring the textarea.
 * Tests fire mouseDown events to trigger onPick.
 *
 * Active styling: the active row has className containing 'bg-indigo-500/[.22]'.
 * User candidate: shows an avatar initial span with the first letter uppercased.
 * Agent candidate: shows an amber 'AGENT' badge span.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MentionDropdown } from './MentionDropdown';
import type { MentionCandidate } from './MentionDropdown';

// ── Test fixtures ─────────────────────────────────────────────────────────────

const USER_ITEM: MentionCandidate = { kind: 'user', id: 'U1', label: 'Alice' };
const AGENT_ITEM: MentionCandidate = { kind: 'agent', slug: 'bot', label: 'Bot' };

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('MentionDropdown', () => {
  // ── Returns null ──────────────────────────────────────────────────────────

  it('renders nothing when open=false', () => {
    const { container } = render(
      <MentionDropdown
        open={false}
        items={[USER_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders nothing when open=true but items is empty', () => {
    const { container } = render(
      <MentionDropdown
        open
        items={[]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  // ── Renders candidates ────────────────────────────────────────────────────

  it('renders each candidate label when open with items', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    expect(screen.getByText('Alice')).toBeDefined();
    expect(screen.getByText('Bot')).toBeDefined();
  });

  // ── Active index ──────────────────────────────────────────────────────────

  it('the activeIndex row has the active class', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={1}
        onPick={vi.fn()}
      />,
    );
    // Bot is at index 1 (activeIndex=1) — should have the active indigo class
    const botLabel = screen.getByText('Bot');
    const botBtn = botLabel.closest('button');
    expect(botBtn?.className).toContain('bg-indigo-500/[.22]');
  });

  it('non-active rows do NOT have the active class', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={1}
        onPick={vi.fn()}
      />,
    );
    // Alice is at index 0, not active
    const aliceLabel = screen.getByText('Alice');
    const aliceBtn = aliceLabel.closest('button');
    expect(aliceBtn?.className).not.toContain('bg-indigo-500/[.22]');
  });

  it('the first row is active when activeIndex=0', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    const aliceLabel = screen.getByText('Alice');
    const aliceBtn = aliceLabel.closest('button');
    expect(aliceBtn?.className).toContain('bg-indigo-500/[.22]');

    const botLabel = screen.getByText('Bot');
    const botBtn = botLabel.closest('button');
    expect(botBtn?.className).not.toContain('bg-indigo-500/[.22]');
  });

  // ── onPick callback ───────────────────────────────────────────────────────

  it('clicking a row calls onPick with the candidate object', () => {
    const onPick = vi.fn();
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={0}
        onPick={onPick}
      />,
    );
    const aliceLabel = screen.getByText('Alice');
    const aliceBtn = aliceLabel.closest('button')!;
    fireEvent.mouseDown(aliceBtn);
    expect(onPick).toHaveBeenCalledWith(USER_ITEM);
  });

  it('clicking the agent row calls onPick with the agent candidate', () => {
    const onPick = vi.fn();
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={0}
        onPick={onPick}
      />,
    );
    const botLabel = screen.getByText('Bot');
    const botBtn = botLabel.closest('button')!;
    fireEvent.mouseDown(botBtn);
    expect(onPick).toHaveBeenCalledWith(AGENT_ITEM);
  });

  it('onPick is called once per mousedown', () => {
    const onPick = vi.fn();
    render(
      <MentionDropdown
        open
        items={[USER_ITEM]}
        activeIndex={0}
        onPick={onPick}
      />,
    );
    const btn = screen.getByText('Alice').closest('button')!;
    fireEvent.mouseDown(btn);
    expect(onPick).toHaveBeenCalledOnce();
  });

  // ── User candidate: avatar initial ────────────────────────────────────────

  it('user candidate shows an avatar initial (first letter of label)', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    // _initial('Alice') → 'A'
    // The initial is in an aria-hidden span with exactly 'A' as text content
    expect(screen.getByText('A')).toBeDefined();
  });

  it('user avatar initial is uppercased first letter', () => {
    const lowerUser: MentionCandidate = { kind: 'user', id: 'U2', label: 'charlie' };
    render(
      <MentionDropdown
        open
        items={[lowerUser]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    // _initial('charlie') → 'C'
    expect(screen.getByText('C')).toBeDefined();
  });

  // ── Agent candidate: amber AGENT badge ────────────────────────────────────

  it('agent candidate shows the amber AGENT badge', () => {
    render(
      <MentionDropdown
        open
        items={[AGENT_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    const agentSpan = screen.getByText('AGENT');
    expect(agentSpan).toBeDefined();
    // The amber treatment
    expect(agentSpan.className).toContain('text-amber-400');
  });

  it('agent candidate does NOT render an avatar initial span', () => {
    render(
      <MentionDropdown
        open
        items={[AGENT_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    // No initial letter — the badge shows 'AGENT' not an initial
    // The first letter 'B' of 'Bot' should NOT appear as an avatar initial
    expect(screen.queryByText('B')).toBeNull();
  });

  // ── Mixed list ────────────────────────────────────────────────────────────

  it('renders user initial AND agent badge together in a mixed list', () => {
    render(
      <MentionDropdown
        open
        items={[USER_ITEM, AGENT_ITEM]}
        activeIndex={0}
        onPick={vi.fn()}
      />,
    );
    // User: avatar initial 'A'
    expect(screen.getByText('A')).toBeDefined();
    // Agent: AGENT badge
    expect(screen.getByText('AGENT')).toBeDefined();
    // Both labels
    expect(screen.getByText('Alice')).toBeDefined();
    expect(screen.getByText('Bot')).toBeDefined();
  });
});
