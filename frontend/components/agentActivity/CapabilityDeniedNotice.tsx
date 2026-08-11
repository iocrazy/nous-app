/**
 * "Agent tried <tool> — blocked by permissions" — Task 6 (Agent 权限页梳理
 * 立项). Surfaces the "capability_denied" transcript events Task 5 now
 * records (one per tool per turn, deduped by seq — see
 * denialsFromTranscriptEvents in toolActivity.ts).
 *
 * `reason` is rendered verbatim, never through t(): it is the capability
 * gate's English abort_reason from the backend, not a UI string owned by
 * this app's i18n catalog.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';

import type { CapabilityDenial } from './toolActivity';

export interface CapabilityDeniedNoticeProps {
  denials: CapabilityDenial[];
  /**
   * Whether the CTA to AI Library settings renders. False on surfaces with
   * no editor/settings context to act from (the issue timeline) — same
   * "no dead affordance beats one that can't act" reasoning as
   * TurnWriteSummary's `interactive` prop.
   */
  interactive?: boolean;
}

// AI Library settings root — per-agent deep-link addressing does not exist
// yet (spec §10, explicitly out of scope for this pass).
const AI_LIBRARY_SETTINGS_PATH = '/settings?tab=ai';

export function CapabilityDeniedNotice({
  denials,
  interactive = true,
}: CapabilityDeniedNoticeProps): React.ReactElement | null {
  const { t } = useTranslation();
  if (denials.length === 0) return null;

  return (
    <div
      data-testid="capability-denied-notice"
      className="flex items-start gap-1.5 rounded-md border border-danger-line bg-danger-soft px-2 py-1.5 text-[11px] text-danger"
    >
      <ShieldAlert size={12} className="mt-0.5 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1 space-y-1">
        {denials.map((denial) => (
          <div key={denial.key}>
            <p>{t('agentActivity.capabilityDenied', { tool: denial.tool })}</p>
            <p className="text-[10px] opacity-80">{denial.reason}</p>
          </div>
        ))}
      </div>
      {interactive && (
        <Link
          to={AI_LIBRARY_SETTINGS_PATH}
          className="shrink-0 whitespace-nowrap text-[10px] text-danger underline hover:no-underline"
        >
          {t('agentActivity.capabilityDeniedCta', 'Open AI Library')}
        </Link>
      )}
    </div>
  );
}
