import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Share2, Copy, ExternalLink, XCircle } from 'lucide-react';
import { getAuthHeaders } from '../services/parserService';

interface Share {
  id: string;
  project_file_id: string | null;
  share_type: string;
  share_code: string;
  password: string | null;
  expires_at: string | null;
  is_active: boolean;
  view_count: number;
  created_at: string;
}

interface ProjectSharesViewProps {
  projectId: string;
  onCountChange?: (count: number) => void;
}

const getApiUrl = (): string => {
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in (import.meta as any).env) {
    return (import.meta as any).env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

export const ProjectSharesView: React.FC<ProjectSharesViewProps> = ({ projectId, onCountChange }) => {
  const { t } = useTranslation();
  const [shares, setShares] = useState<Share[]>([]);
  const [loading, setLoading] = useState(true);

  const loadShares = async () => {
    try {
      setLoading(true);
      const apiUrl = getApiUrl();
      const response = await fetch(`${apiUrl}/api/v1/projects/${projectId}/shares`, {
        headers: await getAuthHeaders(),
      });
      if (!response.ok) throw new Error('Failed to fetch shares');
      const json = await response.json();
      const data = json.data || [];
      setShares(data);
      onCountChange?.(data.filter((s: Share) => s.is_active).length);
    } catch (err) {
      console.error('Failed to load project shares:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadShares(); }, [projectId]);

  const copyShareLink = (share: Share) => {
    const url = `${window.location.origin}/share/${share.share_code}`;
    navigator.clipboard.writeText(url);
  };

  const formatDate = (dateStr: string) => new Date(dateStr).toLocaleDateString();

  const shareTypeColors: Record<string, string> = {
    link: 'text-blue-400 bg-blue-500/20',
    review: 'text-green-400 bg-green-500/20',
    presentation: 'text-purple-400 bg-purple-500/20',
    delivery: 'text-orange-400 bg-orange-500/20',
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-500" />
      </div>
    );
  }

  if (shares.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="p-4 bg-zinc-800 rounded-2xl mb-4">
          <Share2 size={40} className="text-zinc-500" />
        </div>
        <h3 className="text-lg font-medium text-zinc-300 mb-2">
          {t('projects.shares.empty', 'No Shares Yet')}
        </h3>
        <p className="text-sm text-zinc-500">
          {t('projects.shares.emptyHint', 'Share project files to collaborate with others')}
        </p>
      </div>
    );
  }

  return (
    <div className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl overflow-hidden">
      <table className="w-full">
        <thead>
          <tr className="border-b border-zinc-700/50">
            <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.code', 'Share Code')}
            </th>
            <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.type', 'Type')}
            </th>
            <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.views', 'Views')}
            </th>
            <th className="text-center px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.status', 'Status')}
            </th>
            <th className="text-left px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.created', 'Created')}
            </th>
            <th className="text-right px-4 py-3 text-xs font-medium text-zinc-400 uppercase tracking-wider">
              {t('projects.shares.actions', 'Actions')}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-700/30">
          {shares.map(share => (
            <tr key={share.id} className="hover:bg-zinc-700/20 transition-colors">
              <td className="px-4 py-3">
                <span className="text-sm text-white font-mono">{share.share_code}</span>
                {share.password && (
                  <span className="ml-2 text-[10px] text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded">
                    {t('projects.shares.protected', 'Protected')}
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${shareTypeColors[share.share_type] || 'text-zinc-400 bg-zinc-700/50'}`}>
                  {t(`shares.type.${share.share_type}`, share.share_type)}
                </span>
              </td>
              <td className="px-4 py-3 text-center">
                <span className="text-sm text-zinc-300">{share.view_count}</span>
              </td>
              <td className="px-4 py-3 text-center">
                {share.is_active ? (
                  <span className="text-xs text-green-400 bg-green-500/10 px-2 py-0.5 rounded-full">
                    {t('shares.status.active', 'Active')}
                  </span>
                ) : (
                  <span className="text-xs text-zinc-500 bg-zinc-700/50 px-2 py-0.5 rounded-full">
                    {t('shares.status.expired', 'Expired')}
                  </span>
                )}
              </td>
              <td className="px-4 py-3">
                <span className="text-sm text-zinc-500">{formatDate(share.created_at)}</span>
              </td>
              <td className="px-4 py-3 text-right">
                <div className="flex items-center justify-end gap-1">
                  <button
                    onClick={() => copyShareLink(share)}
                    className="p-1.5 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 rounded-lg transition-colors"
                    title={t('projects.shares.copyLink', 'Copy Link')}
                  >
                    <Copy size={14} />
                  </button>
                  <button
                    onClick={() => window.open(`/share/${share.share_code}`, '_blank')}
                    className="p-1.5 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 rounded-lg transition-colors"
                    title={t('projects.shares.openLink', 'Open Link')}
                  >
                    <ExternalLink size={14} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};
