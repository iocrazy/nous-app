/**
 * AIHealthBoard — capability health surface.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AIHealthBoard } from './AIHealthBoard';
import type { CapabilityHealth } from '../services/aiService';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, o?: { count?: number }) =>
      o?.count !== undefined ? `${k}:${o.count}` : k,
  }),
}));

const mockService = vi.hoisted(() => ({ getAIHealth: vi.fn() }));
vi.mock('../services/aiService', () => mockService);

const rows: CapabilityHealth[] = [
  {
    capability: 'summarization',
    label: 'Rewrite (summary)',
    agent_slug: 'summarize',
    assigned: true,
    model: 'qwen-max',
    provider: 'qwen',
    needs_vision: false,
    status: 'ok',
    hint: '',
  },
  {
    capability: 'caption',
    label: 'Image → Prompt',
    agent_slug: 'caption',
    assigned: false,
    model: 'qwen-max',
    provider: 'qwen',
    needs_vision: true,
    status: 'not_vision',
    hint: 'qwen-max is a text-only model but this feature needs to see images.',
  },
];

describe('AIHealthBoard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockService.getAIHealth.mockResolvedValue(rows);
  });

  it('renders a row per capability with its model', async () => {
    render(<AIHealthBoard />);
    expect(await screen.findByText('Rewrite (summary)')).toBeTruthy();
    expect(screen.getByText('summarize → qwen-max')).toBeTruthy();
    expect(screen.getByText('Image → Prompt')).toBeTruthy();
  });

  it('shows the hint for a non-ok capability', async () => {
    render(<AIHealthBoard />);
    expect(
      await screen.findByText(/text-only model but this feature needs to see images/),
    ).toBeTruthy();
  });

  it('shows a warnings badge counting non-ok rows', async () => {
    render(<AIHealthBoard />);
    // 1 non-ok row (caption) → warningsBadge:1
    expect(await screen.findByText('aiHealth.warningsBadge:1')).toBeTruthy();
  });

  it('marks default (unassigned) capabilities', async () => {
    render(<AIHealthBoard />);
    await screen.findByText('Image → Prompt');
    // caption is assigned:false with a model → default badge shows
    expect(screen.getByText('aiHealth.default')).toBeTruthy();
  });

  it('shows a runtime_failing capability with its recent failure count', async () => {
    mockService.getAIHealth.mockResolvedValue([
      {
        capability: 'visual_analysis',
        label: 'Analyze (visual)',
        agent_slug: 'analyze',
        assigned: true,
        model: 'doubao-seed-vl',
        provider: 'doubao',
        needs_vision: true,
        status: 'runtime_failing',
        hint: 'Latest run failed: AccessDenied. Verify the key has access to this model.',
        task_type: 'ai_extract',
        recent_runs: 4,
        recent_failures: 2,
        last_error: 'AccessDenied',
      },
    ]);
    render(<AIHealthBoard />);
    expect(await screen.findByText(/Latest run failed: AccessDenied/)).toBeTruthy();
    // recentFailures chip with both interpolations rendered (no count fallback).
    expect(screen.getByText('aiHealth.recentFailures')).toBeTruthy();
    // runtime_failing counts toward the attention badge.
    expect(screen.getByText('aiHealth.warningsBadge:1')).toBeTruthy();
  });

  it('surfaces a load error', async () => {
    mockService.getAIHealth.mockRejectedValue(new Error('boom'));
    render(<AIHealthBoard />);
    await waitFor(() => expect(screen.getByText('boom')).toBeTruthy());
  });
});
