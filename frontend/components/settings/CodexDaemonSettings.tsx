// components/settings/CodexDaemonSettings.tsx
//
// Settings → AI → Local CLI → GPT CLI card. IC-style platform card
// (2026-08-26 user direction): status badge in the header, an action row
// (Pair / Help / Refresh), collapsible Help with the one-time setup, and a
// device list whose env line IS the "检测 CLI" readout — nous is a cloud
// page, so detection comes from each paired daemon's self-report.
//
// Design: docs/superpowers/specs/2026-08-23-codex-per-user-daemon-design.md

import { HelpCircle, Loader2, Monitor, RefreshCw, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  codexDaemonService,
  isDeviceOnline,
  type CodexDevice,
} from '../../services/codexDaemonService';
import { getApiUrl } from '../../utils/apiConfig';
import { deviceEnvReport, EnvReportLine } from './LocalCliSettings';

const BTN_PRIMARY =
  'flex items-center gap-1.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-1.5 text-xs font-bold disabled:opacity-50';
const BTN_SECONDARY =
  'flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs font-semibold text-content';

export function CodexDaemonSettings() {
  const { t } = useTranslation();
  const [devices, setDevices] = useState<CodexDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [pairCode, setPairCode] = useState<string | null>(null);
  const [helpOpen, setHelpOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      setDevices(await codexDaemonService.listDevices());
      setError(null);
    } catch (err) {
      console.error('[codex-daemon] device list failed:', err);
      setError(t('settings.localCli.errDevices'));
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
      setError(t('settings.localCli.errPairCode'));
    }
  };

  const revoke = async (id: string) => {
    try {
      await codexDaemonService.revokeDevice(id);
      await refresh();
    } catch (err) {
      console.error('[codex-daemon] revoke failed:', err);
      setError(t('settings.localCli.errRevoke'));
    }
  };

  const online = devices.filter((d) => isDeviceOnline(d)).length;
  // What actually runs where, in one line. The server readout below is a
  // detail about the SERVER runtime (it never has the chat CLI); text tasks
  // run on the user's own device, and saying only the server half read as
  // "this feature is image-only" (2026-09-06).
  const onlineDevice = devices.find((d) => isDeviceOnline(d));
  const onlineReport = onlineDevice ? deviceEnvReport(onlineDevice) : null;
  const effectiveKey = !onlineDevice
    ? 'settings.localCli.effectiveNone'
    : onlineReport?.codex_ok && onlineReport?.auth_ok
      ? 'settings.localCli.effectiveDevice'
      : 'settings.localCli.effectiveNotLoggedIn';

  return (
    <div className="space-y-3" data-testid="codex-daemon-settings">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-bold text-content">{t('settings.localCli.codexTitle')}</h3>
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
              ? t('settings.localCli.nOnline', { count: online })
              : devices.length
                ? t('settings.localCli.allOffline')
                : t('settings.localCli.notPaired')}
          </span>
        )}
      </div>
      <p className="text-xs text-content-3">
        {t('settings.localCli.codexDesc')}
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
          {t('settings.localCli.pairDevice')}
        </button>
        <button
          type="button"
          data-testid="codex-help-toggle"
          onClick={() => setHelpOpen((v) => !v)}
          className={BTN_SECONDARY}
        >
          <HelpCircle size={12} /> {t('settings.localCli.help')}
        </button>
        <button type="button" onClick={() => void refresh()} className={BTN_SECONDARY}>
          <RefreshCw size={12} /> {t('settings.localCli.refresh')}
        </button>
      </div>

      {!loading && (
        <div
          data-testid="codex-effective-line"
          className="rounded-xl border border-line px-3 py-2 text-xs text-content"
        >
          {t(effectiveKey, { device: onlineDevice?.device_name ?? '' })}
        </div>
      )}


      {/* The model is chosen on the PROVIDER card (Settings → AI → Providers →
          Codex), together with its enabled-model chips — one knob, one place.
          This card only points there. */}
      <div data-testid="codex-model-hint" className="text-[10px] text-content-3">
        {t('settings.localCli.modelHint')}
      </div>

      {helpOpen && (
        <div className="rounded-xl border border-line p-3 text-xs text-content-3">
          <div>{t('settings.localCli.helpSetup')}</div>
          <code
            data-testid="codex-prereq-command"
            className="mt-1 block whitespace-pre-line rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
          >
            codex login
          </code>
          <div className="mt-2">
            {t('settings.localCli.helpAfter')}
          </div>
          <div className="mt-2">{t('settings.localCli.helpManage')}</div>
          <code
            data-testid="codex-manage-command"
            className="mt-1 block whitespace-pre-line rounded-lg bg-surface-2 px-3 py-2 font-mono text-xs text-content-2"
          >
            node ~/.local/share/nous-codex/nous-codex.mjs status{'\n'}node
            ~/.local/share/nous-codex/nous-codex.mjs uninstall-service
          </code>
          <div className="mt-2">{t('settings.localCli.helpRevoke')}</div>
        </div>
      )}

      {pairCode && (
        <div className="rounded-xl border border-line p-3">
          <div className="text-xs text-content-3">
            {t('settings.localCli.pairInstruction')}
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
            curl -fsSL {getApiUrl()}/api/v1/codex-daemon/dist/install.sh | sh -s -- {pairCode}
          </code>
          <div className="mt-1.5 text-[10px] text-content-3">
            {t('settings.localCli.pairServiceNote')}
          </div>
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-2 text-xs font-semibold text-accent"
          >
            {t('settings.localCli.pairDone')}
          </button>
        </div>
      )}

      <div className="space-y-2">
        {loading ? (
          <div className="flex items-center gap-2 text-xs text-content-3">
            <Loader2 size={12} className="animate-spin" /> {t('settings.localCli.loadingDevices')}
          </div>
        ) : devices.length === 0 ? (
          <div className="text-xs text-content-3">
            {t('settings.localCli.noDevices')}
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
                      ? t('settings.localCli.lastSeen', { time: new Date(d.last_seen_at).toLocaleString() })
                      : t('settings.localCli.neverConnected')}
                  </div>
                  <EnvReportLine report={deviceEnvReport(d)} />
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    isOnline ? 'bg-ok/15 text-ok' : 'bg-line/40 text-content-3'
                  }`}
                >
                  {isOnline ? t('settings.localCli.online') : t('settings.localCli.offline')}
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
