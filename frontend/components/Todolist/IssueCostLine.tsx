/**
 * Compact per-issue AI cost line (W3c) — "AI cost: 12.3k tokens · $0.42".
 *
 * Sits with the pipeline/agent strips in IssueDetailView. Silent when the
 * issue burned nothing (no agent has run yet) so it doesn't add noise to
 * brand-new issues.
 */

import React, { useEffect, useState } from 'react';
import { Coins } from 'lucide-react';

import { usageService, type IssueUsage } from '../../services/usageService';
import { formatIssueCostLine } from '../../pages/usagePanelHelpers';

interface Props {
  issueId: string;
  /** Bumps to refetch after an agent run completes. */
  refreshKey?: number;
}

export function IssueCostLine({ issueId, refreshKey }: Props) {
  const [usage, setUsage] = useState<IssueUsage | null>(null);

  useEffect(() => {
    let cancelled = false;
    usageService
      .getIssueUsage(issueId)
      .then((u) => {
        if (!cancelled) setUsage(u);
      })
      .catch((err) => console.error('[IssueCostLine] load failed', err));
    return () => {
      cancelled = true;
    };
  }, [issueId, refreshKey]);

  if (!usage || usage.total_tokens <= 0) return null;

  return (
    <div className="mt-3 flex items-center gap-1.5 text-xs text-ink-500">
      <Coins size={13} className="text-ink-600" />
      <span>{formatIssueCostLine(usage.total_tokens, usage.cost_cents)}</span>
    </div>
  );
}

export default IssueCostLine;
