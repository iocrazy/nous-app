/**
 * 绑定入口的浏览器服务门（D1）。
 *
 * 每一次扫码绑定都跑在 `nous-browser` 里。那个容器重启（一次后端部署就会）
 * 或宕掉的窗口里，点「QR Code Login」是**必然失败**的 —— 但在这之前，用户
 * 要等扫码弹窗打开、卡在 "Starting..." 直到超时才知道。
 *
 * 这个文件钉的是那句话被提前说了，以及它**只在有真凭据时**才说：
 *
 * * 探测回 `ok:false` → 入口按钮禁用 + 页面上有可见的原因文案；
 * * 探测回 `ok:true` → 按钮照常可用（没有"保险起见先禁用"的默认档）；
 * * 探测请求**自己**失败（我们的 API 不通）→ **不**禁用。那不是关于浏览器
 *   容器的任何证据，凭它编造一次停服比放行更糟；
 * * 页面开着期间服务掉了 → 点下去那一刻的重新探测拦住，并给出类型化提示，
 *   而不是静默不开弹窗；
 * * 只有扫码那半边被禁用 —— 官方授权是跳转平台自己的页面，压根不碰
 *   `nous-browser`，一起灰掉等于砍掉一条还能用的通道；
 * * **不轮询**：挂载一次 + 点击前一次 + 手动 Check again，没有常驻 timer。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ToastProvider } from '../Toast';
import { ConfirmProvider } from '../ConfirmDialog';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const listAccounts = vi.fn();
const getBrowserHealth = vi.fn();
const startSessionLogin = vi.fn();

/** One dead session (the "Scan again" entry) + one live one. */
const ACCOUNTS = [
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
  listPublishTasks: vi.fn().mockResolvedValue([]),
  getBrowserHealth: (...a: unknown[]) => getBrowserHealth(...a),
  startSessionLogin: (...a: unknown[]) => startSessionLogin(...a),
  connectAccount: vi.fn(), refreshAccount: vi.fn(), deleteAccount: vi.fn(),
  getAccountUsage: vi.fn(), submitSmsCode: vi.fn(),
  cancelSessionLogin: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../supabaseClient', () => {
  const channelObj = { on: () => channelObj, subscribe: () => channelObj };
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

const DOWN = { ok: false, error_kind: 'unreachable', message: 'browser service unreachable (ConnectError)' };
const UP = { ok: true, error_kind: null, message: 'ok' };

const mount = () => render(
  <ToastProvider>
    <ConfirmProvider>
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>
    </ConfirmProvider>
  </ToastProvider>,
);

/** Mount and wait for the first probe to have landed. */
const mountSettled = async () => {
  mount();
  await waitFor(() => expect(screen.getByText('Matrix One')).toBeInTheDocument());
  await waitFor(() => expect(getBrowserHealth).toHaveBeenCalled());
};

const openMethodPicker = async () => {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /Connect Account/i }));
  });
};

const qrCard = () => screen.getByRole('button', { name: /QR Code Login/i });
const rescanButton = () => screen.getByRole('button', { name: /Scan again/i });

describe('AccountsPage — browser service gate', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccounts.mockResolvedValue(ACCOUNTS);
    startSessionLogin.mockResolvedValue({ task_id: 'task-1' });
    getBrowserHealth.mockResolvedValue(UP);
  });

  afterEach(() => { vi.useRealTimers(); });

  it('disables the QR entry points and says why when the probe reports down', async () => {
    getBrowserHealth.mockResolvedValue(DOWN);
    await mountSettled();

    // Visible, in words, on the page itself — a greyed button with no
    // explanation just moves the confusion earlier.
    await waitFor(() => expect(
      screen.getByText(/Connection service is temporarily unavailable/i),
    ).toBeInTheDocument());
    expect(screen.getByText(/browser service that is not responding/i)).toBeInTheDocument();

    // The dead-session card's own entry point is gated too.
    expect(rescanButton()).toBeDisabled();

    await openMethodPicker();
    expect(qrCard()).toBeDisabled();
  });

  it('leaves the QR entry points usable when the probe reports healthy', async () => {
    await mountSettled();

    expect(screen.queryByText(/Connection service is temporarily unavailable/i)).toBeNull();
    expect(rescanButton()).toBeEnabled();

    await openMethodPicker();
    expect(qrCard()).toBeEnabled();
  });

  it('does not gate on a failed probe request — that is no evidence about the browser', async () => {
    // Our own API call blew up. It says nothing about nous-browser, and
    // inventing an outage from it would block binding during, say, one flaky
    // request. (The mirror of the backend rule: unhealthy must come from a
    // real probe result.)
    getBrowserHealth.mockRejectedValue(new Error('network'));
    await mountSettled();

    expect(screen.queryByText(/Connection service is temporarily unavailable/i)).toBeNull();
    expect(rescanButton()).toBeEnabled();

    // And the click still works — it re-probes, gets another rejection, and
    // treats that as "unknown", so the modal opens.
    await act(async () => { fireEvent.click(rescanButton()); });
    expect(await screen.findByText(/Sign in with QR code/i)).toBeInTheDocument();
  });

  it('catches a service that went down while the page sat open, with a typed toast', async () => {
    // Healthy at page load, dead by the time the user clicks. Without the
    // pre-click re-probe the gate would only ever be honest for the first
    // few seconds of a session.
    await mountSettled();
    expect(rescanButton()).toBeEnabled();

    getBrowserHealth.mockResolvedValue(DOWN);
    await act(async () => { fireEvent.click(rescanButton()); });

    // The QR modal must NOT have opened...
    expect(screen.queryByText(/Sign in with QR code/i)).toBeNull();
    expect(startSessionLogin).not.toHaveBeenCalled();
    // ...and the refusal is stated, not silent.
    expect(await screen.findByText(/QR sign-in cannot start right now/i)).toBeInTheDocument();
    // The verdict sticks, so the button is now disabled as well.
    await waitFor(() => expect(rescanButton()).toBeDisabled());
  });

  it('keeps Official Authorization available — it never touches the browser service', async () => {
    getBrowserHealth.mockResolvedValue(DOWN);
    await mountSettled();
    await openMethodPicker();

    expect(qrCard()).toBeDisabled();
    expect(screen.getByRole('button', { name: /Official Authorization/i })).toBeEnabled();
  });

  it('recovers through a manual re-check, not a polling loop', async () => {
    getBrowserHealth.mockResolvedValue(DOWN);
    await mountSettled();
    await waitFor(() => expect(rescanButton()).toBeDisabled());

    const afterMount = getBrowserHealth.mock.calls.length;

    // Sitting there must not generate probes on its own: each one launches a
    // real Chromium on the other side.
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    expect(getBrowserHealth.mock.calls.length).toBe(afterMount);

    getBrowserHealth.mockResolvedValue(UP);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Check again/i }));
    });

    await waitFor(() => expect(
      screen.queryByText(/Connection service is temporarily unavailable/i),
    ).toBeNull());
    expect(rescanButton()).toBeEnabled();
  });
});
