import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { PauseCircle, Check, X, Loader2 } from 'lucide-react';

import { aiLibraryService } from '../../services/aiLibraryService';

export interface AwaitingApproval {
  approvalId: string | null;
  reason: string;
  hook?: string;
}

type Resolution = 'pending' | 'approved' | 'rejected' | 'stale';

/**
 * Inline Plan Mode card (Phase 4.5): rendered under an assistant bubble
 * whose turn paused on a hook's await_approval. Approve/Reject hit the
 * same endpoints as the TopBar ApprovalsPanel; a 409 from either means
 * the request was already decided elsewhere — shown as resolved, not an
 * error. After approving, the run resumes on the next message (G1
 * next-turn-replay), so the card says exactly that.
 */
export function ApprovalCard({
  approval,
}: {
  approval: AwaitingApproval;
}): React.ReactElement {
  const { t } = useTranslation();
  const [resolution, setResolution] = useState<Resolution>('pending');
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = useCallback(
    async (action: 'approve' | 'reject') => {
      if (!approval.approvalId || busy) return;
      setBusy(action);
      setError(null);
      try {
        if (action === 'approve') {
          await aiLibraryService.approveRequest(approval.approvalId);
          setResolution('approved');
        } else {
          await aiLibraryService.rejectRequest(approval.approvalId);
          setResolution('rejected');
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (msg.includes('409') || msg.toLowerCase().includes('already')) {
          // Decided from another surface (ApprovalsPanel) — not an error.
          setResolution('stale');
        } else {
          console.error('[ApprovalCard] decide failed:', err);
          setError(msg);
        }
      } finally {
        setBusy(null);
      }
    },
    [approval.approvalId, busy],
  );

  return (
    <div className="mx-3 mt-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
      <div className="flex items-center gap-1.5 text-amber-400 text-xs font-medium">
        <PauseCircle size={13} />
        {t('chat.approvalNeeded')}
      </div>
      {approval.reason && (
        <p className="mt-1 text-xs text-ink-300 break-words">{approval.reason}</p>
      )}

      {resolution === 'pending' && approval.approvalId && (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => decide('approve')}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-green-600/30 transition-colors disabled:opacity-50"
          >
            {busy === 'approve' ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Check size={11} />
            )}
            {t('approvals.approve')}
          </button>
          <button
            type="button"
            disabled={busy !== null}
            onClick={() => decide('reject')}
            className="flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-medium bg-red-600/10 text-red-400 border border-red-500/30 hover:bg-red-600/20 transition-colors disabled:opacity-50"
          >
            {busy === 'reject' ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <X size={11} />
            )}
            {t('approvals.reject')}
          </button>
        </div>
      )}

      {resolution === 'approved' && (
        <p className="mt-2 text-xs text-green-400">{t('chat.approvedContinue')}</p>
      )}
      {resolution === 'rejected' && (
        <p className="mt-2 text-xs text-ink-400">{t('chat.rejectedNote')}</p>
      )}
      {resolution === 'stale' && (
        <p className="mt-2 text-xs text-ink-500">{t('chat.approvalResolved')}</p>
      )}
      {error && <p className="mt-2 text-xs text-red-400 break-words">{error}</p>}
    </div>
  );
}
