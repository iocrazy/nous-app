/**
 * i18n rendering smoke test.
 *
 * Guards the bug class fixed in the AI-Library i18n pass:
 *   1. Missing keys silently masked by `t('x', 'fallback')` defaults — EN
 *      looked fine, ZH (and any no-fallback key) leaked the raw key string.
 *   2. A duplicate top-level `billing` namespace where the later block
 *      shadowed the earlier one (JSON keeps the last duplicate), blanking
 *      out BillingView's entire label set.
 *
 * Strategy: render BillingView under a real en/zh i18n instance built from
 * the shipped locale JSON (not the HTTP backend) and assert (a) the actual
 * translated copy shows up and (b) no `namespace.key` string leaks into the
 * DOM. Plus pure structural guards over the locale files themselves.
 */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';

import enJson from '../public/locales/en.json';
import zhJson from '../public/locales/zh.json';

// react-dom/client needs this flag to run effects under act() in jsdom.
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// BillingView fetches points + payment data on mount. Mock both so it gets
// past the loading gate and renders the label-bearing UI deterministically.
vi.mock('../services/pointsService', () => ({
  fetchPointsBalance: vi.fn(async () => ({
    points_balance: 1234,
    storage_used_bytes: 1024 * 1024,
    storage_limit_bytes: 10 * 1024 * 1024,
    storage_used_percent: 10,
  })),
  fetchPointsPricing: vi.fn(async () => []),
  fetchUsageStats: vi.fn(async () => ({ total_consumed_this_month: 0, top_consumers: [] })),
  adjustPoints: vi.fn(async () => ({ new_balance: 0 })),
}));
vi.mock('../services/paymentService', () => ({
  fetchPackages: vi.fn(async () => []),
  fetchOrders: vi.fn(async () => []),
}));

import { BillingView } from './BillingView';

function makeI18n(lng: 'en' | 'zh'): I18n {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng,
    fallbackLng: 'en',
    resources: { en: { translation: enJson }, zh: { translation: zhJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
}

async function renderToText(node: React.ReactElement): Promise<string> {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(node);
  });
  // Flush the mounted async fetches (Promise.all) + the resulting setState.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
  const text = container.textContent ?? '';
  act(() => root.unmount());
  container.remove();
  return text;
}

// Any "namespace.key" token leaking into rendered text means a missing/
// shadowed translation surfaced its raw key.
const RAW_KEY =
  /\b(billing|tokenUsage|common|members|aiLibrary|sidebar|chat|workforce|shares|search|resources|projects|settings)\.[a-zA-Z][a-zA-Z0-9_]*/;

describe('BillingView i18n rendering', () => {
  it('renders English labels with no raw-key leakage', async () => {
    const text = await renderToText(
      <I18nextProvider i18n={makeI18n('en')}>
        <BillingView teamId="t1" permissions={['team.manage']} onBuyPackage={() => {}} />
      </I18nextProvider>,
    );
    expect(text).toContain('Points Balance');
    expect(text).toContain('Buy Points');
    expect(text).toContain('Storage Usage');
    expect(text).not.toMatch(RAW_KEY);
  });

  it('renders Chinese labels with no raw-key leakage', async () => {
    const text = await renderToText(
      <I18nextProvider i18n={makeI18n('zh')}>
        <BillingView teamId="t1" permissions={['team.manage']} onBuyPackage={() => {}} />
      </I18nextProvider>,
    );
    expect(text).toContain('积分余额');
    expect(text).toContain('购买积分');
    expect(text).toContain('存储用量');
    expect(text).not.toMatch(RAW_KEY);
  });
});

describe('locale structural guards', () => {
  it('has no duplicate top-level namespaces', () => {
    for (const fn of ['en', 'zh'] as const) {
      const raw = readFileSync(resolve(__dirname, `../public/locales/${fn}.json`), 'utf8');
      const top = [...raw.matchAll(/^ {2}"([a-zA-Z0-9_]+)":/gm)].map((m) => m[1]);
      const dups = top.filter((k, i) => top.indexOf(k) !== i);
      expect(dups, `${fn}.json duplicate top-level namespaces`).toEqual([]);
    }
  });

  it('en and zh have identical key sets', () => {
    const flat = (o: Record<string, unknown>, p = ''): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        v && typeof v === 'object'
          ? flat(v as Record<string, unknown>, `${p}${k}.`)
          : [`${p}${k}`],
      );
    const e = new Set(flat(enJson));
    const z = new Set(flat(zhJson));
    expect([...e].filter((k) => !z.has(k)), 'keys only in en').toEqual([]);
    expect([...z].filter((k) => !e.has(k)), 'keys only in zh').toEqual([]);
  });
});
