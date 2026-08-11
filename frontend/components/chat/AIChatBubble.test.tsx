/**
 * AIChatBubble (MessageBubble) — Task 6 wiring: the capability_denied notice.
 *
 * `useRunToolActivity` is mocked wholesale on purpose: its own fetch/cache/
 * dedup behaviour is pinned by toolActivity.test.ts and surfaces.test.tsx.
 * This file only pins that the panel reads `denials` off the hook and
 * renders CapabilityDeniedNotice — never the `activities` half, which would
 * double-render every tool_call (see the header comment in AIChatBubble.tsx
 * and toolActivity.ts's module docstring for why that split exists).
 */

import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { MessageBubble } from './AIChatBubble';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, arg2?: unknown) => {
      if (typeof arg2 === 'string') return arg2;
      if (arg2 && typeof arg2 === 'object') {
        return `${key}:${Object.values(arg2 as Record<string, unknown>).join(',')}`;
      }
      return key;
    },
  }),
}));

const useRunToolActivityMock = vi.fn();
vi.mock('../agentActivity/useRunToolActivity', () => ({
  useRunToolActivity: (...args: unknown[]) => useRunToolActivityMock(...args),
}));

beforeEach(() => {
  useRunToolActivityMock.mockReset();
  useRunToolActivityMock.mockReturnValue({ activities: [], denials: [], loaded: true });
});

describe('AIChatBubble — capability_denied notice wiring', () => {
  it('renders the notice with a CTA when the run has denials', () => {
    useRunToolActivityMock.mockReturnValue({
      activities: [],
      denials: [{ key: 'seq:1', tool: 'CreateShot', reason: 'blocked by permissions' }],
      loaded: true,
    });
    const { container } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content="done" runId="run-1" />
      </MemoryRouter>,
    );
    expect(
      container.querySelector('[data-testid="capability-denied-notice"]'),
    ).not.toBeNull();
    expect(container.querySelector('a[href="/settings?tab=ai"]')).not.toBeNull();
    // Reads (runId, false) — the panel must not poll a settled run's chips a
    // second time; only the live poll flag matters for a running turn.
    expect(useRunToolActivityMock).toHaveBeenCalledWith('run-1', false);
  });

  it('renders nothing extra when the hook reports no denials', () => {
    const { container } = render(
      <MemoryRouter>
        <MessageBubble role="assistant" content="done" runId="run-1" />
      </MemoryRouter>,
    );
    expect(
      container.querySelector('[data-testid="capability-denied-notice"]'),
    ).toBeNull();
  });
});
