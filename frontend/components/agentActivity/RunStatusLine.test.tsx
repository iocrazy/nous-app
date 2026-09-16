/**
 * 3c §4.1 — 正在跑的回合要说清「现在在干什么」，而不是只转一个圈。
 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RunStatusLine } from './RunStatusLine';

// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段（模板 + {{var}} 插值）。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: unknown, opts?: unknown) => {
      const template = typeof fallback === 'string' ? fallback : key;
      const vars = (typeof fallback === 'object' ? fallback : opts) as Record<string, unknown> | undefined;
      return vars ? template.replace(/\{\{(\w+)\}\}/g, (_m, k: string) => String(vars[k] ?? `{{${k}}}`)) : template;
    },
  }),
}));

afterEach(cleanup);

const line = () => screen.getByTestId('run-status-line').textContent;

describe('RunStatusLine', () => {
  it('说清在第几步、在跑什么、跑了多久', () => {
    render(<RunStatusLine current={{ step: 3, tool: 'GenerateImage' }} elapsedMs={4200} />);
    expect(line()).toBe('Step 3 · Running GenerateImage… · 4.2s');
  });

  it('还没进工具时只说步号', () => {
    render(<RunStatusLine current={{ step: 1, tool: null }} elapsedMs={800} />);
    expect(line()).toBe('Step 1 · 0.8s');
  });

  it('回合结束（current 为 null）什么都不画——留在屏幕上的状态行会读成还在跑', () => {
    render(<RunStatusLine current={null} elapsedMs={9999} />);
    expect(screen.queryByTestId('run-status-line')).toBeNull();
  });
});
