// frontend/components/DownloadProgress/SimpleBar.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const SimpleBar: React.FC<DownloadProgressProps> = ({
  percent,
  status,
  onRetry,
  retryCount,
  maxRetries = 3,
}) => {
  return (
    <div className="w-full">
      {status === 'downloading' && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs">
            <span className="text-ink-400">Downloading</span>
            <span className="text-purple-400 font-medium">{percent}%</span>
          </div>
          <div className="h-1.5 bg-ink-800 rounded-full overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-purple-500 to-indigo-500 rounded-full transition-all duration-300"
              style={{ width: `${percent}%` }}
            />
          </div>
        </div>
      )}

      {status === 'completed' && (
        <div className="flex items-center gap-1 text-green-400 text-xs">
          <Check size={12} />
          <span>Done</span>
        </div>
      )}

      {status === 'failed' && (
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 text-red-400 text-xs">
            <AlertCircle size={12} />
            <span>Failed</span>
          </div>
          {onRetry && (
            <button
              onClick={onRetry}
              className="p-1 hover:bg-ink-800 rounded text-ink-400 hover:text-ink-200"
            >
              <RefreshCw size={12} />
            </button>
          )}
        </div>
      )}

      {status === 'retrying' && (
        <div className="flex items-center gap-1 text-orange-400 text-xs">
          <RefreshCw size={12} className="animate-spin" />
          <span>Retry {retryCount}/{maxRetries}</span>
        </div>
      )}

      {status === 'pending' && (
        <div className="flex items-center gap-1 text-ink-500 text-xs">
          <div className="w-3 h-3 rounded-full border border-ink-600 border-t-transparent animate-spin" />
          <span>Queued</span>
        </div>
      )}
    </div>
  );
};

export default SimpleBar;
