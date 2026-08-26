// components/settings/CodexDaemonSettings.tsx
//
// Settings → AI → Local CLI → GPT CLI card. IC-style platform card
// (2026-08-26 user direction): status badge in the header, an action row
// (Pair / Help / Refresh), collapsible Help with the one-time setup, and a
// device list whose env line IS the "检测 CLI" readout — nous is a cloud
// page, so detection comes from each paired daemon's self-report.
//
// Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md

import { HelpCircle, Loader2, Monitor, RefreshCw, Search, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';

import { apiFetch } from '../../services/apiClient';
import {
  codexDaemonService,
  isDeviceOnline,
  type CodexDevice,
} from '../../services/codexDaemonService';
import { deviceEnvReport, EnvReportLine } from './LocalCliSettings';

const BTN_PRIMARY =
  'flex items-center gap-1.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 text-xs font-bold disabled:opacity-50';
const BTN_SECONDARY =
  'flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-semibold text-content';

export function CodexDaemonSettings() {
  const [devices, setDevices] = useState<CodexDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [serverCli, setServerCli] = useState<{
    skill: { installed: boolean; version?: string | null; path?: string | null };
    codex: { installed: boolean; version?: string | null; path?: string | null };
    auth_ok: boolean;
  } | null>(null);
  const [detecting, setDetecting] = useState(false);

  const detectCli = useCallback(async () => {
    setDetecting(true);
    try {
      const res = await apiFetch('/api/v1/codex-cli/status');
      const body = (await res.json()) as { data?: typeof serverCli };
      setServerCli(body.data ?? null);
      setError(null);
    } catch (err) {
      console.error('[codex-cli] detect failed:', err);
      setError('CLI detection failed');
    } finally {
      setDetecting(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const online = devices.filter((d) => isDeviceOnline(d)).length;

  return (
    <div className="space-y-3" data-testid="codex-daemon-settings">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-content">GPT CLI (codex)</h3>
        {!loading && (
          <span
            className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
              online > 0
                ? 'bg-ok/15 text-ok'
                : devices.length
                  ? 'bg-line/40 text-content-3'
                  : 'bg-warn/15 text-warn'
            }`}
          >
            {online > 0
              ? `${online} online`
              : devices.length
                ? 'All offline'
                : 'Not paired'}
          </span>
        )}
      </div>
      <p className="text-xs text-content-3">
        Run canvas generations on your own machine with your own codex login.
        Your credentials never leave your computer — nous only sends the job.
      </p>

      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          data-testid="codex-pair-start"
          onClick={() => void startPairing()}
          className={BTN_PRIMARY}
        >
          Pair a device
        </button>
        <button
          type="button"
          data-testid="codex-detect-cli"
          onClick={() => void detectCli()}
          disabled={detecting}
          className={BTN_SECONDARY}
        >
          {detecting ? <Loader2 size={12} className="animate-spin" /> : <Search size={12} />}
          Detect CLI
        </button>
        <button
          type="button"
          data-testid="codex-help-toggle"
          onClick={() => setHelpOpen((v) => !v)}
          className={BTN_SECONDARY}
        >
          <HelpCircle size={12} /> Help
        </button>
        <button type="button" onClick={() => void refresh()} className={BTN_SECONDARY}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {serverCli && (
        <div
          data-testid="codex-cli-readout"
          className="rounded-xl border border-line bg-surface-2 px-3 py-2 text-xs text-content-2"
        >
          {serverCli.skill.installed ? (
            <>
              gpt-image-2-skill{serverCli.skill.version ? ` ${serverCli.skill.version}` : ''}
              {serverCli.skill.path ? ` · ${serverCli.skill.path}` : ''} · GPT Image 2
              helper installed — canvas image jobs on the server use it.
            </>
          ) : (
            <>gpt-image-2-skill not installed on the server.</>
          )}{' '}
          {serverCli.codex.installed
            ? `codex CLI ${serverCli.codex.version ?? ''} installed.`
            : 'codex chat CLI not installed on the server (image-only).'}{' '}
          {serverCli.auth_ok
            ? 'OAuth session present — login is validated on first run.'
            : 'No OAuth session — the server runtime is not logged in.'}
          <div className="mt-1 text-[10px] text-content-3">
            Server runtime (shared). Your own devices report their CLI state on
            the device rows below after pairing.
          </div>
        </div>
      )}

      {helpOpen && (
        <div className="rounded-xl border border-line p-3 text-xs text-content-3">
          <div>One-time setup on your machine (needs Node 20+ and a ChatGPT subscription):</div>
          <code
            data-testid="codex-prereq-command"
            className="mt-1 block rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
          >
            npm i -g @openai/codex gpt-image-2-skill{'\n'}codex login
          </code>
          <div className="mt-2">
            Then click Pair a device and run the shown commands. Once the daemon
            connects, each device row below shows its CLI check (versions +
            login state) automatically.
          </div>
        </div>
      )}

      {pairCode && (
        <div className="rounded-xl border border-line p-3">
          <div className="text-xs text-content-3">
            Run this on the machine you want to pair (code expires in 10 minutes):
          </div>
          <div
            data-testid="codex-pair-code"
            className="font-mono text-lg font-bold tracking-widest text-content"
          >
            {pairCode}
          </div>
          <code
            data-testid="codex-pair-command"
            className="mt-1 block rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
          >
            curl -fsSL https://raw.githubusercontent.com/iocrazy/nous-app/master/tools/codex-daemon/index.mjs
            -o nous-codex.mjs{'\n'}node nous-codex.mjs pair {pairCode}
            {'\n'}node nous-codex.mjs run
          </code>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-2 text-xs font-semibold text-accent"
          >
            I've run it — refresh the list
          </button>
        </div>
      )}

      <div className="space-y-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-content-3">
            <Loader2 size={12} className="animate-spin" /> Loading devices…
          </div>
        ) : devices.length === 0 ? (
          <div className="text-xs text-content-3">
            No paired devices yet. Once a device connects, its CLI check
            (codex / gpt-image-2-skill / dreamina versions + login state)
            shows here automatically.
          </div>
        ) : (
          devices.map((d) => {
            const isOnline = isDeviceOnline(d);
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
                    isOnline ? 'bg-ok/15 text-ok' : 'bg-line/40 text-content-3'
                  }`}
                >
                  {isOnline ? 'Online' : 'Offline'}
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
