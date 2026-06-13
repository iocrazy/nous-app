import React from 'react';
import { Loader2, Check } from 'lucide-react';

export const AIStatusBadge: React.FC<{ status?: string }> = ({ status }) => {
  switch (status) {
    case 'processing':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400">
          <Loader2 size={9} className="animate-spin" /> Processing
        </span>
      );
    case 'completed':
      return (
        <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">
          <Check size={9} /> Done
        </span>
      );
    case 'failed':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-500/10 text-red-400">
          Failed
        </span>
      );
    case 'pending':
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-ink-500/10 text-ink-500">
          Pending
        </span>
      );
    default:
      return null;
  }
};
