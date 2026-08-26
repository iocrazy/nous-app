// components/settings/LocalCliSettings.tsx
//
// Settings → Local CLI (2026-08-26 tab split, "API 配置和 CLI 配置分开").
// Cloud providers take API keys; this tab manages CLIs that run with the
// user's OWN local login via the paired daemon. Environment status per
// device mirrors IC's 检测 CLI panel — but since nous is a cloud app, the
// facts come from the daemon's self-report instead of a local backend.

import { Check, X } from 'lucide-react';

import type { CodexDevice } from '../../services/codexDaemonService';
import { CodexDaemonSettings } from './CodexDaemonSettings';
import { JimengCliCard } from './JimengCliCard';

export interface DaemonEnvReport {
  codex_ok?: boolean;
  codex_version?: string | null;
  skill_ok?: boolean;
  skill_version?: string | null;
  auth_ok?: boolean;
  dreamina_ok?: boolean;
  dreamina_version?: string | null;
  dreamina_auth_ok?: boolean;
  node_version?: string;
  platform?: string;
}

/** IC-style one-line CLI status for a paired device. */
export function EnvReportLine({ report }: { report: DaemonEnvReport | null | undefined }) {
  if (!report) {
    return (
      <div className="text-[10px] text-content-3">
        Environment not reported yet — starts with the next daemon connect.
      </div>
    );
  }
  const item = (ok: boolean | undefined, label: string) => (
    <span className={`inline-flex items-center gap-0.5 ${ok ? 'text-ok' : 'text-danger'}`}>
      {ok ? <Check size={10} /> : <X size={10} />}
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-2 text-[10px]">
      {item(report.codex_ok, `codex${report.codex_version ? ` ${report.codex_version}` : ''}`)}
      {item(report.skill_ok, `gpt-image-2-skill${report.skill_version ? ` ${report.skill_version}` : ''}`)}
      {item(report.auth_ok, report.auth_ok ? 'logged in' : 'not logged in')}
      {report.dreamina_ok !== undefined &&
        item(
          report.dreamina_ok && report.dreamina_auth_ok,
          report.dreamina_ok
            ? `dreamina${report.dreamina_version ? ` ${report.dreamina_version}` : ''}${report.dreamina_auth_ok ? '' : ' (not logged in)'}`
            : 'dreamina missing',
        )}
      {report.node_version && (
        <span className="text-content-3">node {report.node_version}</span>
      )}
    </div>
  );
}

export function deviceEnvReport(device: CodexDevice): DaemonEnvReport | null {
  return ((device as { env_report?: DaemonEnvReport | null }).env_report) ?? null;
}

export function LocalCliSettings() {
  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <section className="bg-ink-900 border border-ink-800 rounded-xl p-6">
        <CodexDaemonSettings />
      </section>
      <section className="bg-ink-900 border border-ink-800 rounded-xl p-6">
        <JimengCliCard />
      </section>
    </div>
  );
}
