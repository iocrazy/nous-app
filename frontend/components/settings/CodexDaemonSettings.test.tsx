import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CodexDaemonSettings } from './CodexDaemonSettings';

vi.mock('../../services/apiClient', () => ({
  apiFetch: vi.fn().mockResolvedValue({
    json: async () => ({
      data: {
        skill: { installed: true, version: '0.7.3', path: '/usr/local/bin/gpt-image-2-skill' },
        codex: { installed: false, version: null, path: null },
        auth_ok: true,
      },
    }),
  }),
}));
vi.mock('../../services/codexDaemonService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/codexDaemonService')>();
  return {
    ...actual,
    codexDaemonService: {
      createPairCode: vi.fn().mockResolvedValue({ code: 'ABCD2345', expires_in_seconds: 600 }),
      listDevices: vi.fn().mockResolvedValue([
        {
          id: '1',
          device_name: 'mac-mini',
          platform: 'darwin',
          created_at: '2026-08-23T00:00:00Z',
          last_seen_at: new Date().toISOString(),
        },
        {
          id: '2',
          device_name: 'old-box',
          platform: 'linux',
          created_at: '2026-08-01T00:00:00Z',
          last_seen_at: '2026-08-01T00:00:00Z',
        },
      ]),
      revokeDevice: vi.fn().mockResolvedValue(undefined),
    },
  };
});

import { codexDaemonService } from '../../services/codexDaemonService';
import en from '../../public/locales/en.json';
import zh from '../../public/locales/zh.json';

describe('CodexDaemonSettings', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists devices with an online/offline badge from the heartbeat', async () => {
    render(<CodexDaemonSettings />);
    await waitFor(() => expect(screen.getByText('mac-mini')).toBeInTheDocument());
    expect(screen.getByTestId('codex-device-1').textContent).toMatch(/online/i);
    expect(screen.getByTestId('codex-device-2').textContent).toMatch(/offline/i);
  });

  it('shows the pairing code and the one-line installer that also registers a service', async () => {
    render(<CodexDaemonSettings />);
    fireEvent.click(screen.getByTestId('codex-pair-start'));
    await waitFor(() => expect(screen.getByTestId('codex-pair-code')).toBeInTheDocument());
    expect(screen.getByTestId('codex-pair-code').textContent).toContain('ABCD2345');
    // The old copy told users to run `node nous-codex.mjs run` in a terminal,
    // which dies with the terminal — the installer registers a login service.
    const command = screen.getByTestId('codex-pair-command').textContent ?? '';
    expect(command).toContain('tools/codex-daemon/install.sh');
    expect(command).toContain('sh -s -- ABCD2345');
    expect(command).not.toContain('nous-codex.mjs run');
    // One-time setup moved into the collapsible Help (IC-style card).
    fireEvent.click(screen.getByTestId('codex-help-toggle'));
    expect(screen.getByTestId('codex-prereq-command').textContent).toContain(
      'codex login',
    );
    expect(screen.getByTestId('codex-manage-command').textContent).toContain(
      'uninstall-service',
    );
  });

  it('revokes a device and refreshes the list', async () => {
    render(<CodexDaemonSettings />);
    await waitFor(() => expect(screen.getByTestId('codex-device-1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('codex-revoke-1'));
    await waitFor(() =>
      expect(codexDaemonService.revokeDevice).toHaveBeenCalledWith('1'),
    );
  });
  it('Detect CLI shows the IC-style server readout', async () => {
    render(<CodexDaemonSettings />);
    fireEvent.click(screen.getByTestId('codex-detect-cli'));
    await waitFor(() =>
      expect(screen.getByTestId('codex-cli-readout')).toBeInTheDocument(),
    );
    const text = screen.getByTestId('codex-cli-readout').textContent ?? '';
    expect(text).toContain('gpt-image-2-skill 0.7.3');
    expect(text).toContain('/usr/local/bin/gpt-image-2-skill');
    expect(text).toContain('settings.localCli.authPresent');
  });
});

// ── 只读沙箱披露(终审 I-3 / spec §6) ──────────────────────────────────────
//
// 实测结论:codex 的 `-s read-only --ephemeral -C <空目录>` 限制的是**写**,不限制
// 读——$HOME 下与任务无关的文件可以被原样读出并返回。spec §6 预先授权了"带这个
// 缺陷上线",条件是 README **与设置页**都显著标注。
//
// README 是仓库里的文件,绝大多数用户永远不会看到;用户实际在的地方是设置页的
// GPT CLI 卡。所以这段文案本身就是那条授权的兑现物,而不是可选的润色——没有守卫
// 的话,任何一次"文案太长了精简一下"都能把唯一的披露删掉且不产生任何信号。
describe('read-only sandbox disclosure', () => {
  it.each([
    ['en', en, [/read-only sandbox/i, /can read files on your machine/i, /untrusted text/i]],
    ['zh', zh, [/只读沙箱/, /可以读取你电脑上的文件/, /不可信文本/]],
  ])('%s codexDesc says the sandbox does not stop reads', (_locale, bundle, patterns) => {
    const desc = (bundle as Record<string, any>).settings.localCli.codexDesc as string;
    for (const p of patterns) expect(desc).toMatch(p);
  });
});

// ── the model is chosen on the PROVIDER card, not here (2026-09-05) ───────
// The selector that lived here for a day moved to Settings → AI → Providers →
// Codex ("要像 Doubao 这样能够配置"): one knob, one place. This card only points
// at it.
describe('CodexDaemonSettings — model lives on the provider card', () => {
  it('shows the pointer and no selector of its own', async () => {
    render(<CodexDaemonSettings />);
    expect(await screen.findByTestId('codex-model-hint')).toBeInTheDocument();
    expect(screen.queryByTestId('codex-model-select')).toBeNull();
  });
});
