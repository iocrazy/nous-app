import React from 'react';
import { CheckCircle2, XCircle, AlertTriangle, Clock } from 'lucide-react';

interface ReviewStatusBadgeProps {
  /** A `review_status.status`; the column has no CHECK, so an unknown value
   * renders as Pending instead of crashing the panel. */
  status: string;
  size?: 'sm' | 'md';
}

type KnownStatus = 'pending' | 'approved' | 'needs_changes' | 'rejected';

const statusConfig: Record<KnownStatus, {
  icon: typeof Clock;
  label: string;
  className: string;
}> = {
  pending: {
    icon: Clock,
    label: 'Pending',
    className: 'bg-ink-500/15 text-ink-400',
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
  const config = statusConfig[status as KnownStatus] ?? statusConfig.pending;
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
