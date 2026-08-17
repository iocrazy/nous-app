import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { vi, describe, it, expect } from 'vitest';

// vi.mock is hoisted above top-level consts, so the fns it references must be
// hoisted too (vi.hoisted) — otherwise "Cannot access before initialization".
const { retryPublishTask, getShareSchema } = vi.hoisted(() => ({
  retryPublishTask: vi.fn().mockResolvedValue({}),
  getShareSchema: vi.fn().mockResolvedValue({ schema_url: 'snssdk1128://x', share_id: 's1' }),
}));

vi.mock('../../services/distributionService', () => ({
  listPublishTasks: vi.fn().mockResolvedValue([
    { id: '700', content_type: 'video', title: 'Awaiting', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'pending_share',
      created_at: '2026-07-08T00:00:00Z', schedule_state: 'none',
      accounts: [{ id: '1', account_id: '10', username: 'HEYGO', avatar_url: null,
        channel: 'h5', status: 'pending_share', error_message: null, published_url: null,
        platform_item_id: null, published_at: null }] },
    { id: '701', content_type: 'video', title: 'Mixed', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'partial',
      created_at: '2026-07-08T00:00:00Z', schedule_state: 'none',
      accounts: [
        // Only this row carries an avatar (it is joined live off
        // social_accounts) — 'Bad One' next to it keeps exercising the
        // gradient-tile fallback (D3).
        { id: '2', account_id: '11', username: 'Ok One',
          avatar_url: 'https://p3-pc.douyinpic.com/aweme/100x100/okone.jpeg', channel: 'official',
          status: 'success', error_message: null, published_url: 'https://douyin/v/9',
          platform_item_id: '9', published_at: '2026-07-08T01:00:00Z' },
        { id: '3', account_id: '12', username: 'Bad One', avatar_url: null, channel: 'official',
          status: 'failed', error_message: 'upload rejected', published_url: null,
          platform_item_id: null, published_at: null }] },
    { id: '703', content_type: 'video', title: 'Session No Link', description: null, topics: [],
      visibility: 'private', distribution_mode: 'broadcast', status: 'success',
      created_at: '2026-07-08T00:00:00Z', schedule_state: 'none',
      // 会话通道的常态:发布成功,但拿不到作品直链(抖音发布后重定向不带
      // item id,作品卡无 href/id,列表接口也不返回)。
      // …and this row also carries a caveat: the post went out, but with the
      // closest matching track rather than the one that was typed. The row is
      // still success — the caveat has to be readable WITHOUT being an error.
      accounts: [{ id: '5', account_id: '14', username: 'Sessioned', avatar_url: null,
        channel: 'session', status: 'success',
        error_message: "[music_approximate] published with '起风了 (Cover)' — the closest match the platform's search returned for '起风了'",
        published_url: null,
        platform_item_id: null, published_at: '2026-07-08T03:00:00Z',
        // Real wire shape for a session row nobody has read back yet: the
        // columns exist and are NULL. NULL is the START of pending, not
        // "nothing to check".
        verify_state: null, verify_detail: null }] },
    { id: '702', content_type: 'video', title: 'Done One', description: null, topics: [],
      visibility: 'public', distribution_mode: 'broadcast', status: 'success',
      created_at: '2026-07-08T00:00:00Z', schedule_state: 'none',
      accounts: [{ id: '4', account_id: '13', username: 'Winner', avatar_url: null,
        channel: 'official', status: 'success', error_message: null,
        published_url: 'https://douyin/v/12', platform_item_id: '12',
        published_at: '2026-07-08T02:00:00Z' }] },
  ]),
  cancelPublishTask: vi.fn(),
  retryPublishTask,
  getShareSchema,
  // Our own read-back cadence, fetched so the page never writes those numbers
  // down itself. Resolving null here is the "could not load" branch, which must
  // still render the page — see RecordsPage.readback.test.tsx.
  getReadbackTiming: vi.fn().mockResolvedValue(null),
  listAccounts: vi.fn().mockResolvedValue([
    { id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op1',
      username: 'HEYGO', avatar_url: null, token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '11', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op2',
      username: 'Ok One', avatar_url: null, token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '12', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op3',
      username: 'Bad One', avatar_url: null, token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '13', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op4',
      username: 'Winner', avatar_url: null, token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
    { id: '14', scope_type: 'user', scope_id: 'u1', platform: 'douyin', platform_user_id: 'op5',
      username: 'Sessioned', avatar_url: null, token_expires_at: null, status: 'active', created_at: '2026-07-08T00:00:00Z' },
  ]),
}));

// RecordsPage calls useToast — mock it so the test needn't wrap ToastProvider.
vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

// RecordsPage subscribes to task_tracking via useTaskManager (DBOS live
// sync) — mock the context so the test needn't wrap TaskManagerProvider.
vi.mock('../../contexts/TaskManagerContext', () => ({
  useTaskManager: () => ({ tasks: [] }),
}));

import RecordsPage from './RecordsPage';

describe('RecordsPage', () => {
  it('shows pending_share prompt and failed retry', async () => {
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Awaiting')).toBeInTheDocument());
    expect(screen.getByText(/Open Douyin to finish/i)).toBeInTheDocument();

    // expand the mixed task to reveal per-account rows
    fireEvent.click(screen.getByText('Mixed'));
    await waitFor(() => expect(screen.getByText('upload rejected')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Retry/i }));
    // Both are explicit on the wire: the endpoint distinguishes "try the same
    // thing again" from "drop the schedule and go now" and from "drop the
    // track and go without music", and only the last two are allowed to change
    // what the user asked for.
    await waitFor(() => expect(retryPublishTask)
      .toHaveBeenCalledWith('701', 'as_scheduled', { dropMusic: false }));
  });

  it('draws the real avatar on expanded account rows, gradient tile for the rest', async () => {
    // The record sub-rows drew the gradient tile for every account even
    // though avatar_url ships on each one (D3).
    const { container } = render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Awaiting')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Mixed'));
    await waitFor(() => expect(screen.getByText('upload rejected')).toBeInTheDocument());

    const img = screen.getByRole('img', { name: 'Ok One' });
    expect(img).toHaveAttribute('src', 'https://p3-pc.douyinpic.com/aweme/100x100/okone.jpeg');
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');

    expect(container.querySelectorAll('.sub-row .ava').length).toBe(2);
    expect(container.querySelectorAll('.sub-row .ava img').length).toBe(1);
    expect(screen.getByText('BA')).toBeInTheDocument();

    // A dead CDN drops back to the tile rather than a broken-image glyph.
    fireEvent.error(img);
    await waitFor(() => expect(container.querySelectorAll('.sub-row .ava img').length).toBe(0));
    expect(screen.getByText('OK')).toBeInTheDocument();
  });

  it('filters by status chips', async () => {
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Awaiting')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Needs action/i }));
    // needs_action includes pending_share AND partial (both have user actions);
    // success is excluded.
    expect(screen.getByText('Awaiting')).toBeInTheDocument();   // pending_share
    expect(screen.getByText('Mixed')).toBeInTheDocument();      // partial
    expect(screen.queryByText('Done One')).not.toBeInTheDocument(); // success excluded
  });

  it('会话通道没有直链时,给出平台管理页入口而不是假装有作品链接', async () => {
    // 这是会话通道的**常态**而非异常:抖音发布后重定向不带 item id,作品卡
    // 上没有 href 也没有 id 属性,列表接口同样不返回(2026-08-08 对着真实
    // 控制台逐条验证)。猜"最新那张卡"会在有定时/并发发布的账号上张冠李戴,
    // 比不给链接更糟。
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Session No Link')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Session No Link'));

    const link = await screen.findByText('Open in platform');
    expect(link.closest('a')).toHaveAttribute(
      'href',
      'https://creator.douyin.com/creator-micro/content/manage',
    );
  });

  it('近似匹配的配乐用翻译文案回显,而不是把后端英文原样打给用户', async () => {
    // 后端那句 prose 是写给日志的(它带着平台原文曲名与 reason code)。用户
    // 看到的必须是按 reason 键控的翻译文案 —— 这与提交时那道门的口径同族:
    // reason 是契约,message 不是。
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Session No Link')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Session No Link'));

    expect(
      await screen.findByText(/closest matching track rather than the one you named/i),
    ).toBeInTheDocument();
    // 后端原句不出现在页面上(它仍留在 title 属性里供排查)。
    expect(screen.queryByText(/the closest match the platform.s search returned/i)).toBeNull();
  });

  it('兜底入口的文案不能写成 View post —— 那会谎报跳转目标', async () => {
    render(<MemoryRouter><RecordsPage /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('Session No Link')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Session No Link'));

    const link = await screen.findByText('Open in platform');
    // 指向管理页的入口,文案必须说"去平台",不能借用作品直链那句 View post。
    expect(link.closest('a')).toHaveAttribute(
      'href',
      'https://creator.douyin.com/creator-micro/content/manage',
    );
    expect(link.textContent).not.toMatch(/View post/i);
  });
});
