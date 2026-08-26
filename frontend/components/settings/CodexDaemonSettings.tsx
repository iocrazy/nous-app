// components/settings/CodexDaemonSettings.tsx
//
// Settings → Local codex (C 方案 C5). Pair this account with a daemon that
// runs on the user's OWN machine and holds their OWN codex login, so canvas
// generations spend their subscription — not the server owner's — and the
// credentials never leave their computer.
//
// Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md

import { Loader2, Monitor, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import {
  codexDaemonService,
  isDeviceOnline,
  type CodexDevice,
} from '../../services/codexDaemonService';
import { deviceEnvReport, EnvReportLine } from './LocalCliSettings';

export function CodexDaemonSettings() {
  const [devices, setDevices] = useState<CodexDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setDevices(await codexDaemonService.listDevices());
      setError(null);
    } catch (err) {
      console.error('[codex-daemon] device list failed:', err);
      setError('Could not load your devices');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    // Heartbeats land every 30s — re-poll so the badge stops lying quickly.
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const startPairing = async () => {
    try {
      const { code } = await codexDaemonService.createPairCode();
      setPairCode(code);
      setError(null);
    } catch (err) {
      console.error('[codex-daemon] pair code failed:', err);
      setError('Could not create a pairing code');
    }
  };

  const revoke = async (id: string) => {
    try {
      await codexDaemonService.revokeDevice(id);
      await refresh();
    } catch (err) {
      console.error('[codex-daemon] revoke failed:', err);
      setError('Could not revoke that device');
    }
  };

  return (
    <div className="space-y-4" data-testid="codex-daemon-settings">
      <div>
        <h3 className="text-sm font-bold text-content">Local codex</h3>
        <p className="mt-1 text-xs text-content-3">
          Run canvas generations on your own machine with your own codex login.
          Your credentials never leave your computer — nous only sends the job.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div className="rounded-xl border border-line p-3">
        {pairCode ? (
          <div className="space-y-2">
            <div className="text-xs text-content-3">
              One-time setup on your machine (needs Node 20+ and a ChatGPT
              subscription):
            </div>
            <code
              data-testid="codex-prereq-command"
              className="block rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
            >
              npm i -g @openai/codex gpt-image-2-skill{'\n'}codex login
            </code>
            <div className="text-xs text-content-3">
              Then pair this device (code expires in 10 minutes):
            </div>
            <div
              data-testid="codex-pair-code"
              className="font-mono text-lg font-bold tracking-widest text-content"
            >
              {pairCode}
            </div>
            <code
              data-testid="codex-pair-command"
              className="block rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
            >
              curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs
              -o nous-codex.mjs{'\n'}node nous-codex.mjs pair {pairCode}
              {'\n'}node nous-codex.mjs run
            </code>
            <button
              type="button"
              onClick={() => void refresh()}
              className="text-xs font-semibold text-accent"
            >
              I've run it — refresh the list
            </button>
          </div>
        ) : (
          <button
            type="button"
            data-testid="codex-pair-start"
            onClick={() => void startPairing()}
            className="rounded-full bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 text-xs font-bold"
          >
            Pair a device
          </button>
        )}
      </div>

      <div className="space-y-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-content-3">
            <Loader2 size={12} className="animate-spin" /> Loading devices…
          </div>
        ) : devices.length === 0 ? (
          <div className="text-xs text-content-3">
            No paired devices yet. Once a device connects, its CLI check
            (codex / gpt-image-2-skill / dreamina versions + login state)
            shows here automatically — the pairing IS the detection: unlike a
            local app, a cloud page cannot probe your machine directly.
          </div>
        ) : (
          devices.map((d) => {
            const online = isDeviceOnline(d);
            return (
              <div
                key={d.id}
                data-testid={`codex-device-${d.id}`}
                className="flex items-center gap-2 rounded-xl border border-line px-3 py-2"
              >
                <Monitor size={14} className="text-content-3" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-semibold text-content">
                    {d.device_name}
                  </div>
                  <div className="text-[10px] text-content-3">
                    {d.platform || 'unknown'} ·{' '}
                    {d.last_seen_at
                      ? `last seen ${new Date(d.last_seen_at).toLocaleString()}`
                      : 'never connected'}
                  </div>
                  <EnvReportLine report={deviceEnvReport(d)} />
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    online ? 'bg-ok/15 text-ok' : 'bg-line/40 text-content-3'
                  }`}
                >
                  {online ? 'Online' : 'Offline'}
                </span>
                <button
                  type="button"
                  data-testid={`codex-revoke-${d.id}`}
                  aria-label={`Revoke ${d.device_name}`}
                  onClick={() => void revoke(d.id)}
                  className="text-content-3 hover:text-danger"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
