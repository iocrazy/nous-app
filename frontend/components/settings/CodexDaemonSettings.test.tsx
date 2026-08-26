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

describe('CodexDaemonSettings', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists devices with an online/offline badge from the heartbeat', async () => {
    render(<CodexDaemonSettings />);
    await waitFor(() => expect(screen.getByText('mac-mini')).toBeInTheDocument());
    expect(screen.getByTestId('codex-device-1').textContent).toMatch(/online/i);
    expect(screen.getByTestId('codex-device-2').textContent).toMatch(/offline/i);
  });

  it('shows the pairing code and the exact command to run', async () => {
    render(<CodexDaemonSettings />);
    fireEvent.click(screen.getByTestId('codex-pair-start'));
    await waitFor(() => expect(screen.getByTestId('codex-pair-code')).toBeInTheDocument());
    expect(screen.getByTestId('codex-pair-code').textContent).toContain('ABCD2345');
    expect(screen.getByTestId('codex-pair-command').textContent).toContain(
      'node nous-codex.mjs pair ABCD2345',
    );
    // One-time setup moved into the collapsible Help (IC-style card).
    fireEvent.click(screen.getByTestId('codex-help-toggle'));
    expect(screen.getByTestId('codex-prereq-command').textContent).toContain(
      'codex login',
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
