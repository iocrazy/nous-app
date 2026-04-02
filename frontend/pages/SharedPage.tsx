// frontend/pages/SharedPage.tsx

/**
 * Share management page — lists all shares created by the current user.
 *
 * Features:
 * - List shares with status (active/expired/cancelled)
 * - Copy share link
 * - Cancel active shares
 * - Filter by status
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Link2,
  Eye,
  Presentation,
  Package,
  Copy,
  Check,
  XCircle,
  Trash2,
  Loader2,
  ExternalLink,
  Filter,
} from 'lucide-react';
import { fetchShares, cancelShare as cancelShareApi, deleteSharePermanent } from '../services/sharesService';
import { useConfirm } from '../components/ConfirmDialog';
import { useTeamContext } from '../contexts/TeamContext';
import { Share, ShareType, ShareStatus } from '../types';

const SHARE_TYPE_ICONS: Record<ShareType, React.ReactNode> = {
  link: <Link2 size={16} />,
  review: <Eye size={16} />,
  presentation: <Presentation size={16} />,
  delivery: <Package size={16} />,
};

const STATUS_COLORS: Record<string, string> = {
  active: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  inactive: 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20',
  expired: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  cancelled: 'bg-red-500/10 text-red-400 border-red-500/20',
};

export const SharedPage: React.FC = () => {
  const { t } = useTranslation();
  const { selectedTeamId } = useTeamContext();
  const [shares, setShares] = useState<Share[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<ShareStatus | 'all'>('all');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const confirm = useConfirm();

  const loadShares = useCallback(async () => {
    setLoading(true);
    try {
      const params: { status?: string; team_id?: string } = {};
      if (filter !== 'all') params.status = filter;
      params.team_id = selectedTeamId || 'personal';
      const data = await fetchShares(params);
      setShares(data);
    } catch {
      /* ignore */
    }
    setLoading(false);
  }, [filter, selectedTeamId]);

  useEffect(() => {
    loadShares();
  }, [loadShares]);

  const handleCopyLink = async (share: Share) => {
    const url = `${window.location.origin}/share/${share.share_code}`;
    await navigator.clipboard.writeText(url);
    setCopiedId(share.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleToggleActive = async (shareId: string) => {
    try {
      await cancelShareApi(shareId);
      await loadShares();
    } catch {
      /* ignore */
    }
  };

  const handleDeleteShare = async (shareId: string) => {
    const ok = await confirm({
      title: 'Delete Share',
      message: 'This share record will be permanently deleted. This cannot be undone.',
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteSharePermanent(shareId);
      await loadShares();
    } catch (err) {
      console.error('Failed to delete share:', err);
    }
  };

  const filteredShares = shares;

  return (
    <div className="flex-1 overflow-auto p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-zinc-100">{t('shared.title')}</h1>

        {/* Filter */}
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-zinc-500" />
          {(['all', 'active', 'inactive', 'expired'] as const).map((status) => (
            <button
              key={status}
              onClick={() => setFilter(status)}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
                filter === status
                  ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/30'
                  : 'text-zinc-500 hover:text-zinc-300 border border-transparent'
              }`}
            >
              {status === 'all' ? t('common.all') : t(`shared.status.${status}`)}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 size={24} className="animate-spin text-zinc-500" />
        </div>
      ) : filteredShares.length === 0 ? (
        <div className="text-center py-20">
          <Link2 size={48} className="text-zinc-700 mx-auto mb-4" />
          <p className="text-zinc-400 font-medium">{t('shared.empty')}</p>
          <p className="text-sm text-zinc-600 mt-1">{t('shared.emptyHint')}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filteredShares.map((share) => (
            <div
              key={share.id}
              className="flex items-center gap-4 px-4 py-3 bg-zinc-900/50 border border-zinc-800/50 rounded-xl hover:bg-zinc-800/30 transition-colors group"
            >
              {/* Icon */}
              <div className="w-9 h-9 rounded-lg bg-zinc-800 flex items-center justify-center text-zinc-400">
                {SHARE_TYPE_ICONS[share.share_type as ShareType] || <Link2 size={16} />}
              </div>

              {/* Name + type */}
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-zinc-200 truncate">{share.share_name}</p>
                <p className="text-xs text-zinc-500">{t(`shared.type.${share.share_type}`)}</p>
              </div>

              {/* Status + Views + Date — fixed width for alignment */}
              <div className="flex items-center gap-4 shrink-0">
                {/* Status badge — clickable to toggle active ↔ inactive */}
                {(() => {
                  const isActive = share.status === 'active';
                  const isInactive = share.status === 'inactive' || share.status === 'cancelled';
                  const isExpired = share.status === 'expired';
                  return (isActive || isInactive) ? (
                    <button
                      onClick={() => handleToggleActive(share.id)}
                      className={`px-2.5 py-0.5 text-xs font-medium rounded-md border w-16 text-center transition-colors cursor-pointer ${
                        isActive
                          ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20'
                          : 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20 hover:bg-zinc-500/20'
                      }`}
                      title={isActive ? 'Click to deactivate' : 'Click to reactivate'}
                    >
                      {isActive ? 'Active' : 'Inactive'}
                    </button>
                  ) : isExpired ? (
                    <span className="px-2.5 py-0.5 text-xs font-medium rounded-md border w-16 text-center bg-amber-500/10 text-amber-400 border-amber-500/20">
                      Expired
                    </span>
                  ) : (
                    <span className={`px-2.5 py-0.5 text-xs font-medium rounded-md border w-16 text-center ${STATUS_COLORS[share.status] || STATUS_COLORS.active}`}>
                      {share.status}
                    </span>
                  );
                })()}

                <span className="text-xs text-zinc-500 w-16 text-right tabular-nums">
                  {share.view_count || 0} {t('share.views')}
                </span>

                <span className="text-xs text-zinc-600 w-20 text-right tabular-nums">
                  {new Date(share.created_at).toLocaleDateString()}
                </span>
              </div>

              {/* Actions */}
              <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 w-20 justify-end">
                <button
                  onClick={() => handleCopyLink(share)}
                  className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                  title={t('shared.copyLink')}
                >
                  {copiedId === share.id ? <Check size={14} className="text-emerald-400" /> : <Copy size={14} />}
                </button>
                <a
                  href={`/share/${share.share_code}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="p-1.5 text-zinc-500 hover:text-zinc-300 transition-colors"
                >
                  <ExternalLink size={14} />
                </a>
                <button
                  onClick={() => handleDeleteShare(share.id)}
                  className="p-1.5 text-zinc-500 hover:text-red-400 transition-colors"
                  title="Delete"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
