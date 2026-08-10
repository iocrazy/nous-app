/**
 * 解绑确认框（P0-2 / mig 416）。
 *
 * 这个按钮以前直接进库:没有确认框,后端硬 DELETE,而
 * `publish_task_accounts.account_id` 是 ON DELETE CASCADE —— 一次点击可以静默
 * 抹掉一个账号的全部发布记录。2026-08-09 实测生产库,屏幕上两张卡分别背着 10 条
 * 和 0 条,用户点掉的恰好是 0 条那张。
 *
 * 单独成文件而不是并进 AccountsPage.test.tsx:那边的 react-i18next mock 直接返回
 * 原始默认值、**不做插值**(它自己就断言 `'{{n}} posts'` 这个字面量)。而本文件要
 * 验的恰恰是"弹窗里出现的是后端返回的那个数字",没有插值就验不了。所以这里用一个
 * 会插值的 mock,顺带保证两边各自的断言都说的是实话。
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { ToastProvider } from '../Toast';
import { ConfirmProvider } from '../ConfirmDialog';

// 真的做 {{k}} 插值 —— 否则"弹窗显示 10"这条断言会在模板字面量上通过。
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, def?: string | Record<string, unknown>, vars?: Record<string, unknown>) => {
      const template = typeof def === 'string' ? def : key;
      const values = (typeof def === 'string' ? vars : def) ?? {};
      return template.replace(/\{\{(\w+)\}\}/g, (_m, name) => String(values[name] ?? `{{${name}}}`));
    },
  }),
}));

const listAccounts = vi.fn();
const getAccountUsage = vi.fn();
const deleteAccount = vi.fn();

const ACCOUNT = {
  id: '335617669826935', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'uid-tt', username: 'MioPoo', avatar_url: null,
  token_expires_at: null, auth_type: 'session', status: 'active',
  session_checked_at: '2026-08-08T00:00:00Z', created_at: '2026-08-06T00:00:00Z',
};

vi.mock('../../services/distributionService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../services/distributionService')>()),
  listAccounts: (...a: unknown[]) => listAccounts(...a),
  // 一条覆盖该账号的发布任务 —— 页面据此算出 per-account 的 "1 post"。
  // 它是**近似值**(只取最近 100 个任务),弹窗绝不能用它,见下面那条用例。
  listPublishTasks: vi.fn().mockResolvedValue([
    {
      id: '900', content_type: 'video', title: 'Launch', description: null,
      topics: [], visibility: 'public', distribution_mode: 'broadcast',
      status: 'success', created_at: new Date().toISOString(),
      accounts: [
        { id: '1', account_id: '335617669826935', username: 'MioPoo',
          avatar_url: null, channel: 'h5', status: 'success',
          error_message: null, published_url: null, platform_item_id: null,
          published_at: null },
      ],
    },
  ]),
  getAccountUsage: (...a: unknown[]) => getAccountUsage(...a),
  deleteAccount: (...a: unknown[]) => deleteAccount(...a),
  connectAccount: vi.fn(), refreshAccount: vi.fn(),
  startSessionLogin: vi.fn(), submitSmsCode: vi.fn(),
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

const mount = () => render(
  <ToastProvider>
    <ConfirmProvider>
      <MemoryRouter>
        <AccountsPage />
      </MemoryRouter>
    </ConfirmProvider>
  </ToastProvider>,
);

const clickUnbind = async () => {
  mount();
  await waitFor(() => expect(screen.getByText('MioPoo')).toBeInTheDocument());
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: /^Unbind$/i }));
  });
};

describe('AccountsPage — unbind confirmation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listAccounts.mockResolvedValue([ACCOUNT]);
    getAccountUsage.mockResolvedValue({ publish_records: 10 });
    deleteAccount.mockResolvedValue(undefined);
  });

  it('shows the real record count and does not unbind until confirmed', async () => {
    await clickUnbind();

    expect(getAccountUsage).toHaveBeenCalledWith('335617669826935');
    expect(await screen.findByText('Unbind MioPoo?')).toBeInTheDocument();
    // 数字本身,不是模板、不是形容词。
    expect(screen.getByText(/Its 10 publish records are kept\./)).toBeInTheDocument();
    // 弹窗还开着 = 一个字节都还没删。
    expect(deleteAccount).not.toHaveBeenCalled();
  });

  it('quotes the server count, not the count derived from the stats call', async () => {
    // 页面自己算得出 "1 post"(listPublishTasks 只取最近 100 个任务)。账号历史
    // 一长,那个近似值就偏小 —— 而偏小恰恰发生在这次点击代价最大的时候。
    await clickUnbind();

    expect(await screen.findByText(/Its 10 publish records are kept\./)).toBeInTheDocument();
    expect(screen.queryByText(/Its 1 publish records are kept\./)).toBeNull();
  });

  it('reports zero as zero instead of hiding the line', async () => {
    // 用户当时点掉的正是 0 条那张卡 —— 而他无从知道另一张背着 10 条。
    // "0" 是答案,不是"没有影响所以不用说"。
    getAccountUsage.mockResolvedValue({ publish_records: 0 });
    await clickUnbind();

    expect(await screen.findByText(/Its 0 publish records are kept\./)).toBeInTheDocument();
  });

  it('cancelling leaves the account bound', async () => {
    await clickUnbind();
    await screen.findByText('Unbind MioPoo?');

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /^Cancel$/i }));
    });

    expect(deleteAccount).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText('Unbind MioPoo?')).toBeNull());
  });

  it('confirming unbinds and refetches the list', async () => {
    await clickUnbind();
    await screen.findByText('Unbind MioPoo?');
    const before = listAccounts.mock.calls.length;

    // 弹窗开着时页面上有两个 "Unbind":卡片上那个和弹窗的确认键。
    // ConfirmProvider 把弹窗渲染在 children 之后,所以确认键是最后一个。
    const buttons = screen.getAllByRole('button', { name: /^Unbind$/i });
    await act(async () => { fireEvent.click(buttons[buttons.length - 1]); });

    await waitFor(() => expect(deleteAccount).toHaveBeenCalledWith('335617669826935'));
    await waitFor(() => expect(listAccounts.mock.calls.length).toBeGreaterThan(before));
  });

  it('aborts with a typed message when the count cannot be fetched', async () => {
    // 查不到影响范围就退回"确定吗?"式的空确认,等于把这次修复还原成原样。
    // 解绑从来不紧急,宁可让用户重试。
    getAccountUsage.mockRejectedValue(new Error('boom'));
    await clickUnbind();

    expect(await screen.findByText(/Could not check what this account is used by/i))
      .toBeInTheDocument();
    expect(screen.queryByText('Unbind MioPoo?')).toBeNull();
    expect(deleteAccount).not.toHaveBeenCalled();
  });
});
