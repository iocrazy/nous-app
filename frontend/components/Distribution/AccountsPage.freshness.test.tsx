/**
 * 账号卡上的「上次校验于何时」。
 *
 * 缺的东西不是一个字段 —— 后端从 mig 398 起就一直在返回 `session_checked_at`
 * （`SocialAccountOut`），只是**前端一个字都没读过**。后果是「一小时前刚验过、
 * 确实健康」和「绑上之后巡检从没轮到过它」在页面上完全同形：都是一枚绿色
 * `Active`。第二种被显示成「这个号能用」，而真相是「我们不知道」。
 *
 * 这个文件钉的就是那两种状态在**渲染结果上真的不同**，而不是差一个不显眼的
 * 字：文案不同、`unverified` 类不同、图标不同。只断言「有一行字」是抓不住
 * 回退的 —— 一个把 null 当成「刚查过」的实现照样会渲染出一行字。
 *
 * 时间相关的分档逻辑在 `accountStatus.freshness.test.ts` 里逐档验过；这里只
 * 负责「页面把它说出来了、且两种状态可分辨」。
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ToastProvider } from '../Toast';
import { ConfirmProvider } from '../ConfirmDialog';

// ⚠️ 这个 t 桩**真的做插值**，与仓库里其他 Distribution 用例的 `d ?? _k` 不同。
// 那种桩会把 'Checked {{n}}h ago' 原样渲染出来，于是「3 小时」这个真正的事实
// 从没被任何断言碰过 —— 一个永远填 0 的实现照样全绿。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: string | Record<string, unknown>, opts?: Record<string, unknown>) => {
      const template = typeof def === 'string' ? def : key;
      const vars = (typeof def === 'string' ? opts : def) ?? {};
      return template.replace(
        /\{\{(\w+)\}\}/g,
        (_m: string, name: string) => String(vars[name] ?? `{{${name}}}`),
      );
    },
  }),
}));

const listAccounts = vi.fn();

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** `ms` 毫秒之前的 ISO 串 —— 后端发过来的就是这个形状（Pydantic datetime）。 */
const agoIso = (ms: number) => new Date(Date.now() - ms).toISOString();

/**
 * 四种账号并排，覆盖本次改动的每一条分支。
 *
 * ⚠️ `Never Checked` 这一行（`session_checked_at: null`）正是全仓库既有 fixture
 * 从来没有过的形态 —— 盲区所在。
 */
const ACCOUNTS = [
  {
    id: '727145299382534148', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
    platform_user_id: 'op4', username: 'Checked Recently', avatar_url: null,
    token_expires_at: null, auth_type: 'session', status: 'active',
    // 多加一秒，免得刚好落在 3h 的边界上抖成 2h。
    session_checked_at: agoIso(3 * HOUR + 1000), created_at: '2026-07-04T00:00:00Z',
  },
  {
    id: '727145299382534149', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
    platform_user_id: 'op5', username: 'Never Checked', avatar_url: null,
    token_expires_at: null, auth_type: 'session', status: 'active',
    session_checked_at: null, created_at: '2026-07-03T00:00:00Z',
  },
  {
    id: '727145299382534150', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
    platform_user_id: 'op6', username: 'Dead Session', avatar_url: null,
    token_expires_at: null, auth_type: 'session', status: 'needs_relogin',
    session_checked_at: agoIso(5 * DAY + 1000), created_at: '2026-07-02T00:00:00Z',
  },
  {
    id: '727145299382534145', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
    platform_user_id: 'op1', username: 'Official Bound', avatar_url: null,
    token_expires_at: null, auth_type: 'oauth', status: 'active',
    created_at: '2026-07-07T00:00:00Z',
  },
];

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  listAccounts: (...a: unknown[]) => listAccounts(...a),
  listPublishTasks: vi.fn().mockResolvedValue([]),
  getBrowserHealth: vi.fn().mockResolvedValue({ ok: true, error_kind: null, message: 'ok' }),
  connectAccount: vi.fn(), refreshAccount: vi.fn(), deleteAccount: vi.fn(),
  getAccountUsage: vi.fn(), startSessionLogin: vi.fn(), submitSmsCode: vi.fn(),
  cancelSessionLogin: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../supabaseClient', () => {
  const channelObj = { on: () => channelObj, subscribe: () => channelObj };
  return {
    getSupabaseClient: () => ({
      channel: () => channelObj,
      removeChannel: vi.fn(),
    }),
  };
});

import AccountsPage from './AccountsPage';

/** 某个账号的卡片（用卡上的名字定位，再往上找到 `.acct` 容器）。 */
const card = (username: string): HTMLElement => {
  const name = screen.getByText(username);
  const el = name.closest('.acct');
  if (!el) throw new Error(`no .acct card for ${username}`);
  return el as HTMLElement;
};

/** 卡上的校验新鲜度行，没有就是 null。 */
const checkLine = (username: string) =>
  within(card(username)).queryByTestId('acct-check');

const mountSettled = async () => {
  render(
    <ToastProvider>
      <ConfirmProvider>
        <MemoryRouter>
          <AccountsPage />
        </MemoryRouter>
      </ConfirmProvider>
    </ToastProvider>,
  );
  await waitFor(() => expect(screen.getByText('Never Checked')).toBeInTheDocument());
};

describe('AccountsPage — session verification freshness', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccounts.mockResolvedValue(ACCOUNTS);
  });

  it('states when a session account was last verified, with the real elapsed time', async () => {
    await mountSettled();

    const line = checkLine('Checked Recently');
    expect(line).not.toBeNull();
    // 真的把 3 小时说出来了 —— 不是一句没有数字的「已校验」。
    expect(line).toHaveTextContent('Checked 3h ago');
    // 而且它是「有证据」那一档：不带 unverified 类。
    expect(line!.className).not.toMatch(/unverified/);
  });

  it('never renders an unchecked account as if it were verified', async () => {
    await mountSettled();

    const line = checkLine('Never Checked');
    expect(line).not.toBeNull();
    // 措辞要说得出「我们还不知道」，而不是任何形式的「已校验」。
    expect(line).toHaveTextContent(/Not checked yet/i);
    expect(line).toHaveTextContent(/unconfirmed/i);
    // 这条是关键的反向断言：坏版本（把 null 当刚查过）会在这里红。
    expect(line!.textContent ?? '').not.toMatch(/Checked \d+[mhd] ago/);
    expect(line!.textContent ?? '').not.toMatch(/just now/i);
  });

  it('makes the two states visually distinguishable, not just differently worded', async () => {
    await mountSettled();

    const verified = checkLine('Checked Recently')!;
    const unverified = checkLine('Never Checked')!;

    // 三条轴同时不同 —— 文案、类名、图标。任何一条单独退化都会红。
    expect(verified.textContent).not.toEqual(unverified.textContent);
    expect(unverified.className).toMatch(/unverified/);
    expect(verified.className).not.toMatch(/unverified/);
    expect(unverified.querySelector('svg')?.getAttribute('class'))
      .not.toEqual(verified.querySelector('svg')?.getAttribute('class'));
  });

  it('still says when a dead session last worked', async () => {
    // `session_checked_at` 的含义是「最后一次成功确认」，掉线时后端刻意不动它
    // （session_health_check 的写回规则）。所以这个数字仍然有意义：它说的是
    // 「这个号是从那时之后的某一刻起不行的」。
    await mountSettled();

    expect(checkLine('Dead Session')).toHaveTextContent('Checked 5d ago');
  });

  it('says nothing about session checks on an oauth account', async () => {
    // 巡检只扫 session 行，oauth 的 `session_checked_at` 结构性永远是 null。
    // 给它标「从未校验」会是一个凭空造出来的警报。
    await mountSettled();

    expect(checkLine('Official Bound')).toBeNull();
  });
});
