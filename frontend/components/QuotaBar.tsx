import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { fetchPointsBalance } from '../services/pointsService';
import { TeamQuota } from '../types';

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export const QuotaBar: React.FC = () => {
  const { t } = useTranslation();
  const [quota, setQuota] = useState<TeamQuota | null>(null);

  useEffect(() => {
    fetchPointsBalance()
      .then(setQuota)
      .catch(() => {});
  }, []);

  if (!quota || quota.storage_limit_bytes === 0) return null;

  const percent = Math.min(
    Math.round((quota.storage_used_bytes / quota.storage_limit_bytes) * 100),
    100
  );

  const barColor =
    percent > 95
      ? 'from-red-500 to-red-400'
      : percent > 80
        ? 'from-amber-500 to-amber-400'
        : 'from-purple-500 to-indigo-500';

  return (
    <div className="flex items-center gap-2" title={`${formatBytes(quota.storage_used_bytes)} / ${formatBytes(quota.storage_limit_bytes)}`}>
      <span className="text-[10px] text-zinc-500 hidden lg:inline">
        {t('points.storage', 'Storage')}
      </span>
      <div className="w-24 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div
          className={`h-full bg-gradient-to-r ${barColor} rounded-full transition-all duration-300`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-[10px] text-zinc-400 font-medium">
        {percent}%
      </span>
    </div>
  );
};
