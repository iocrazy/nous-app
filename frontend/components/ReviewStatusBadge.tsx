import React from 'react';
import { CheckCircle2, XCircle, AlertTriangle, Clock } from 'lucide-react';

interface ReviewStatusBadgeProps {
  status: 'pending' | 'approved' | 'needs_changes' | 'rejected';
  size?: 'sm' | 'md';
}

const statusConfig = {
  pending: {
    icon: Clock,
    label: 'Pending',
    className: 'bg-zinc-500/15 text-zinc-400',
  },
  approved: {
    icon: CheckCircle2,
    label: 'Approved',
    className: 'bg-emerald-500/15 text-emerald-400',
  },
  needs_changes: {
    icon: AlertTriangle,
    label: 'Needs Changes',
    className: 'bg-amber-500/15 text-amber-400',
  },
  rejected: {
    icon: XCircle,
    label: 'Rejected',
    className: 'bg-red-500/15 text-red-400',
  },
};

export const ReviewStatusBadge: React.FC<ReviewStatusBadgeProps> = ({ status, size = 'sm' }) => {
  const config = statusConfig[status];
  const Icon = config.icon;
  const iconSize = size === 'sm' ? 12 : 14;
  const textSize = size === 'sm' ? 'text-[10px]' : 'text-xs';
  const padding = size === 'sm' ? 'px-1.5 py-0.5' : 'px-2 py-1';

  return (
    <span className={`inline-flex items-center gap-1 ${padding} ${textSize} font-medium rounded-md ${config.className}`}>
      <Icon size={iconSize} />
      {config.label}
    </span>
  );
};
