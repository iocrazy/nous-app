/** 3c §4.2：尾部「共消耗」一行。积分是**真的扣了的那个数**，¢ 只是没扣成时的兜底；
 *  两个都没有读作 `—`，编出来的 0 会把「没算出价」说成「免费」（同 `fmtChildCents`）。 */
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RunCostTail } from './RunCostTail';

// i18n mock：与 nodes/outputCards.test.tsx:18-26 逐字同一段。
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

const base = { costCents: 0.82, chargedPoints: null, model: 'doubao-seed-2-0-lite', status: 'completed' };
const text = () => screen.getByTestId('run-cost-tail').textContent;
const title = () => screen.getByTestId('run-cost-tail').getAttribute('title');

describe('RunCostTail', () => {
  it('扣过积分就显示积分', () => {
    render(<RunCostTail {...base} chargedPoints={0.82} />);
    expect(text()).toBe('◇ 0.82 · doubao-seed-2-0-lite');
  });

  it('没扣成（BYOK / 急停 / 零花费）退回 ¢', () => {
    render(<RunCostTail {...base} />);
    expect(text()).toBe('¢0.82 · doubao-seed-2-0-lite');
  });

  it('两个都没有读作 —，不是 ¢0.00', () => {
    render(<RunCostTail {...base} costCents={null} />);
    expect(text()).toBe('— · doubao-seed-2-0-lite');
  });

  it('模型未知时只留数字，不拼一个空的 ·', () => {
    render(<RunCostTail {...base} model={null} />);
    expect(text()).toBe('¢0.82');
  });

  it('运行中的值加 … ——读者要知道它还会变', () => {
    render(<RunCostTail {...base} chargedPoints={0.4} live />);
    expect(text()).toBe('◇ 0.40… · doubao-seed-2-0-lite');
  });

  it('hover 浮层三行：tokens · ¢ · 扣没扣', () => {
    render(<RunCostTail {...base} chargedPoints={0.82} promptTokens={1200} completionTokens={340} />);
    expect(title()).toBe('1200 prompt · 340 completion tokens\n¢0.82\nCharged ◇ 0.82');
  });

  it('没扣的浮层说清为什么——一句 not charged 不说原因等于没说', () => {
    render(<RunCostTail {...base} promptTokens={10} completionTokens={2} status="failed" />);
    expect(title()).toBe('10 prompt · 2 completion tokens\n¢0.82\nNot charged (failed)');
  });

  it('连 status 都没有时不编一个原因出来', () => {
    render(<RunCostTail {...base} status={null} promptTokens={1} completionTokens={1} />);
    expect(title()).toBe('1 prompt · 1 completion tokens\n¢0.82\nNot charged');
  });
});
