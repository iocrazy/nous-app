import React, { useState, useEffect } from 'react';
import { Coins } from 'lucide-react';
import { fetchPointsBalance } from '../services/pointsService';
import { TeamQuota } from '../types';

interface PointsBadgeProps {
  onClick: () => void;
}

export const PointsBadge: React.FC<PointsBadgeProps> = ({ onClick }) => {
  const [quota, setQuota] = useState<TeamQuota | null>(null);

  useEffect(() => {
    let cancelled = false;

    fetchPointsBalance()
      .then((data) => {
        if (!cancelled) {
          setQuota(data);
        }
      })
      .catch(() => {
        // Silently ignore errors; badge simply won't render
      });

    return () => {
      cancelled = true;
    };
  }, []);

  if (!quota) {
    return null;
  }

  return (
    <button
      onClick={onClick}
      title="Points Balance"
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 transition-colors text-sm font-medium"
    >
      <Coins size={16} />
      <span>{quota.points_balance.toLocaleString()}</span>
    </button>
  );
};
