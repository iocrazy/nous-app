import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ToastProvider } from '../Toast';
// 解绑走 useConfirm(P0-2:那个按钮以前直接进库,而后端硬 DELETE 会级联抹掉
// 发布记录)。少了这个 Provider,AccountsPage 一挂载就抛。
import { ConfirmProvider } from '../ConfirmDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const connectAccount = vi.fn();
const startSessionLogin = vi.fn();
// Hoisted so a test can count refetches — the close-path reload is only
// observable as "listAccounts ran again".
const listAccounts = vi.fn();

const ACCOUNTS = [
    {
      id: '727145299382534145', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
      token_expires_at: null, auth_type: 'oauth', status: 'active',
      created_at: '2026-07-07T00:00:00Z',
    },
    {
      id: '727145299382534146', scope_type: 'team', scope_id: 't1', platform: 'douyin',
      platform_user_id: 'op2', username: 'Studio Official', avatar_url: null,
      token_expires_at: null, auth_type: 'oauth', status: 'expired',
      created_at: '2026-07-06T00:00:00Z',
    },
    {
      id: '727145299382534147', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op3', username: 'Matrix One', avatar_url: null,
      token_expires_at: null, auth_type: 'session', status: 'needs_relogin',
      session_checked_at: '2026-08-01T00:00:00Z', created_at: '2026-07-05T00:00:00Z',
    },
    {
      id: '727145299382534148', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
      platform_user_id: 'op4', username: 'Matrix Two', avatar_url: null,
      token_expires_at: null, auth_type: 'session', status: 'active',
      session_checked_at: '2026-08-03T00:00:00Z', created_at: '2026-07-04T00:00:00Z',
    },
];

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  listAccounts: (...a: unknown[]) => listAccounts(...a),
  listPublishTasks: vi.fn().mockResolvedValue([
    {
      id: '900', content_type: 'video', title: 'Launch', description: null,
      topics: [], visibility: 'public', distribution_mode: 'broadcast',
      status: 'success', created_at: new Date().toISOString(),
      accounts: [
        { id: '1', account_id: '727145299382534145', username: 'HEYGO',
          avatar_url: null, channel: 'h5', status: 'success',
          error_message: null, published_url: null, platform_item_id: null,
          published_at: null },
      ],
    },
  ]),
  connectAccount: (...a: unknown[]) => connectAccount(...a),
  refreshAccount: vi.fn(), deleteAccount: vi.fn(),
  startSessionLogin: (...a: unknown[]) => startSessionLogin(...a),
  submitSmsCode: vi.fn(), cancelSessionLogin: vi.fn().mockResolvedValue(undefined),
}));

// The session modal streams over Realtime; the page tests only need it to mount.
// 捕获 postgres_changes 的回调,好在用例里真的触发一次事件 —— 只断言
// "订阅建立了" 证明不了收到事件会做正确的事。
const realtimeHandlers = vi.hoisted(() => ({ current: [] as Array<() => void> }));

vi.mock('../../supabaseClient', () => {
  const channelObj = {
    on: (_evt: string, _cfg: unknown, cb: () => void) => {
      realtimeHandlers.current.push(cb);
      return channelObj;
    },
    subscribe: () => channelObj,
  };
  return {
    getSupabaseClient: () => ({
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({
          eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }),
        }),
      }),
    }),
  };
});

import AccountsPage from './AccountsPage';

const mount = () => render(
  <ToastProvider>
    <ConfirmProvider>
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>
    </ConfirmProvider>
  </ToastProvider>,
);

describe('AccountsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccounts.mockResolvedValue(ACCOUNTS);
    connectAccount.mockResolvedValue({ auth_url: 'https://open.douyin.com/oauth' });
    startSessionLogin.mockResolvedValue({ task_id: 'task-1' });
  });

  it('renders accounts with status and scope badges', async () => {
    mount();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    expect(screen.getByText('Studio Official')).toBeInTheDocument();
    // Expired appears as the status chip (the stats note now varies by mix).
    expect(screen.getAllByText(/Authorization expired/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Reauthorize/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect Account/i)).toBeInTheDocument();
    // v4 additions: stats strip + platform panel + per-account usage meta.
    expect(screen.getByText(/Posts this week/i)).toBeInTheDocument();
    expect(screen.getByText(/Needs attention/i)).toBeInTheDocument();
    expect(screen.getByText(/Connect a platform/i)).toBeInTheDocument();
    // The i18next mock returns raw defaults without interpolation, so the
    // per-account usage meta renders its literal template.
    expect(screen.getByText('{{n}} posts')).toBeInTheDocument();
  });

  it('counts needs_relogin in "Needs attention"', async () => {
    const { container } = mount();
    await waitFor(() => expect(screen.getByText('Matrix One')).toBeInTheDocument());
    const stat = screen.getByText('Needs attention').closest('.stat')!;
    // One expired OAuth token + one dead session. Counting only 'expired'
    // would render 1 and quietly hide an offline account.
    expect(within(stat as HTMLElement).getByText('2')).toBeInTheDocument();
    expect(stat).toHaveClass('warn');
    // Mixed causes → the note must not claim they are all expired tokens.
    expect(within(stat as HTMLElement).getByText(/expired or signed out/i)).toBeInTheDocument();
    expect(container.querySelectorAll('.acct.warn')).toHaveLength(2);
  });

  it('labels each account with its binding method', async () => {
    mount();
    await waitFor(() => expect(screen.getByText('Matrix Two')).toBeInTheDocument());
    expect(screen.getAllByText('QR session')).toHaveLength(2);
    expect(screen.getAllByText('Official')).toHaveLength(2);
    // A dead session reads as signed out, not as an expired authorization.
    expect(screen.getByText('Session signed out')).toBeInTheDocument();
  });

  it('routes the recovery action by auth_type', async () => {
    mount();
    await waitFor(() => expect(screen.getByText('Matrix One')).toBeInTheDocument());
    // oauth + expired → reauthorize at the platform.
    expect(screen.getByRole('button', { name: /Reauthorize/i })).toBeInTheDocument();
    // session + needs_relogin → a new scan, which is the only way back.
    const rescan = screen.getByRole('button', { name: /Scan again/i });
    await act(async () => { fireEvent.click(rescan); });
    expect(await screen.findByText(/Sign in with QR code/i)).toBeInTheDocument();
    await waitFor(() => expect(startSessionLogin).toHaveBeenCalledWith({
      platform: 'douyin', scope_type: 'user', scope_id: 'u1',
    }));
    expect(connectAccount).not.toHaveBeenCalled();
  });

  it('refetches the list when the QR dialog closes, not only when it succeeds', async () => {
    // A user bound an account and the card appeared only after refreshing the
    // page by hand. The backend was clean: the row commits ~200ms BEFORE
    // `status: success` is broadcast, so any refetch after that sees it. The
    // gap was that the only refetch hung off a single websocket event — miss
    // it (dropped frame, re-subscribe, dialog closed a beat early) and the
    // list quietly stays stale.
    //
    // Closing is the last moment to repair that for free, so both paths
    // refetch. What this pins is the redundancy: `onBound` alone passed the
    // old tests and still shipped the bug.
    mount();
    await waitFor(() => expect(screen.getByText('Matrix One')).toBeInTheDocument());
    const before = listAccounts.mock.calls.length;

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Scan again/i }));
    });
    expect(await screen.findByText(/Sign in with QR code/i)).toBeInTheDocument();

    const closers = screen.getAllByRole('button', { name: /^(Close|Cancel)$/i });
    await act(async () => { fireEvent.click(closers[closers.length - 1]); });

    await waitFor(() =>
      expect(listAccounts.mock.calls.length).toBeGreaterThan(before));
  });

  it('asks which binding method to use instead of jumping to OAuth', async () => {
    mount();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Connect Account/i }));
    });
    expect(screen.getByText(/Choose how to connect/i)).toBeInTheDocument();
    expect(screen.getByText('QR Code Login')).toBeInTheDocument();
    expect(screen.getByText('Official Authorization')).toBeInTheDocument();
    // Opening the picker must not have started anything on its own.
    expect(connectAccount).not.toHaveBeenCalled();
    expect(startSessionLogin).not.toHaveBeenCalled();
  });

  it('starts the QR flow from the picker', async () => {
    mount();
    await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Connect another account/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByText('QR Code Login'));
    });
    await waitFor(() => expect(startSessionLogin).toHaveBeenCalledWith({
      platform: 'douyin', scope_type: 'user', scope_id: 'self',
    }));
  });

  it('starts the OAuth flow from the picker', async () => {
    const origin = window.location;
    Object.defineProperty(window, 'location', {
      writable: true, configurable: true, value: { ...origin, href: '' },
    });
    try {
      mount();
      await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
      await act(async () => {
        fireEvent.click(screen.getByRole('button', { name: /Connect Account/i }));
      });
      await act(async () => {
        fireEvent.click(screen.getByText('Official Authorization'));
      });
      await waitFor(() => expect(connectAccount).toHaveBeenCalledWith({
        platform: 'douyin', scope_type: 'user', scope_id: 'self',
      }));
      expect(startSessionLogin).not.toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'location', {
        writable: true, configurable: true, value: origin,
      });
    }
  });

  it('states each binding method separately on the Douyin platform card', async () => {
    mount();
    await waitFor(() => expect(screen.getByText(/Connect a platform/i)).toBeInTheDocument());
    // The old blanket "Ready" badge overstated a half-working OAuth channel.
    //
    // 查询限定在抖音那张卡内:小红书 / B 站接进来之后,"QR sign-in ready"
    // 在页面上出现三次,全局 getByText 会直接抛 "Found multiple elements"。
    // 这个用例要验的是**抖音卡片上两种方式各自的状态**,不是全页唯一性。
    const douyinCard = screen.getByText('Douyin').closest('button') as HTMLElement;
    expect(douyinCard).toBeTruthy();
    expect(within(douyinCard).getByText(/QR sign-in ready/i)).toBeInTheDocument();
    expect(within(douyinCard).getByText(/Official: hand-off only/i)).toBeInTheDocument();
    expect(screen.queryByText(/^Ready$/)).toBeNull();
  });

  it('小红书 / B 站:能扫码绑定,但明确标注发布未实现', async () => {
    // 只标 "QR sign-in ready" 会让人以为绑完就能发,而实际提交会被后端
    // 类型化拒绝(publishing_not_implemented)。UI 必须把这两件事分开说。
    mount();
    await waitFor(() => expect(screen.getByText(/Connect a platform/i)).toBeInTheDocument());

    for (const name of ['Xiaohongshu', 'Bilibili']) {
      const card = screen.getByText(name).closest('button') as HTMLElement;
      expect(card).toBeTruthy();
      expect(within(card).getByText(/QR sign-in ready/i)).toBeInTheDocument();
      expect(within(card).getByText(/Publishing not available yet/i)).toBeInTheDocument();
    }
  });
});

describe('AccountsPage — 账号列表的实时刷新', () => {
  it('订阅 social_accounts 的变化,收到事件时重新拉列表', async () => {
    // 用户两次反馈:扫码绑定成功但卡片不出现,手动刷新才看到。
    // 后端顺序是对的(先入库再报 success),缺的是这个列表没有独立的实时
    // 信号 —— 刷新全靠 onBound/onClose 那**同一个** HTTP 请求,它一失败
    // (截图里就有一批 503)列表就永远是旧的。
    realtimeHandlers.current = [];
    mount();
    await waitFor(() => expect(listAccounts).toHaveBeenCalled());

    expect(realtimeHandlers.current.length).toBeGreaterThan(0);

    const before = (listAccounts as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    act(() => { realtimeHandlers.current.forEach((cb) => cb()); });

    await waitFor(() => {
      const after = (listAccounts as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
      expect(after).toBeGreaterThan(before);
    });
  });
});
