// frontend/components/DownloadProgress/NeonBorder.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const NeonBorder: React.FC<DownloadProgressProps> = ({
  percent,
  status,
  speed,
  onRetry,
  size = 'normal',
  thumbnailUrl,
  retryCount,
  maxRetries = 3,
}) => {
  const isNormal = size === 'normal';
  const containerSize = isNormal ? 'w-full h-full min-h-[300px]' : 'w-24 h-24';

  return (
    <div className={`relative ${containerSize} rounded-xl overflow-hidden group`}>
      {/* Background thumbnail with blur */}
      {thumbnailUrl && (
        <div className="absolute inset-0">
          <img
            src={thumbnailUrl}
            alt=""
            className="absolute inset-0 w-full h-full object-contain"
            referrerPolicy="no-referrer"
          />
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
        </div>
      )}

      {/* Neon border animation */}
      <svg
        className="absolute inset-0 w-full h-full"
        style={{ filter: status === 'downloading' ? 'drop-shadow(0 0 8px rgba(139, 92, 246, 0.8))' : undefined }}
      >
        <defs>
          <linearGradient id="neonGradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#a855f7" />
            <stop offset="50%" stopColor="#6366f1" />
            <stop offset="100%" stopColor="#a855f7" />
          </linearGradient>
        </defs>
        <rect
          x="2"
          y="2"
          width="calc(100% - 4px)"
          height="calc(100% - 4px)"
          rx="12"
          fill="none"
          stroke="rgba(139, 92, 246, 0.2)"
          strokeWidth="2"
        />
        <rect
          x="2"
          y="2"
          width="calc(100% - 4px)"
          height="calc(100% - 4px)"
          rx="12"
          fill="none"
          stroke="url(#neonGradient)"
          strokeWidth="3"
          strokeDasharray={`${percent * 3.6} 360`}
          strokeLinecap="round"
          className="transition-all duration-300"
          style={{
            animation: status === 'downloading' ? 'pulse 2s ease-in-out infinite' : undefined,
          }}
        />
      </svg>

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
        {status === 'downloading' && (
          <>
            <span className={`font-bold text-white ${isNormal ? 'text-4xl' : 'text-lg'}`}>
              {percent}%
            </span>
            {speed && isNormal && (
              <span className="text-purple-300 text-sm mt-1">{speed}</span>
            )}
          </>
        )}

        {status === 'completed' && (
          <div className={`${isNormal ? 'p-4' : 'p-2'} rounded-full bg-green-500/20 animate-in zoom-in duration-300`}>
            <Check className={`text-green-400 ${isNormal ? 'w-12 h-12' : 'w-6 h-6'}`} />
          </div>
        )}

        {status === 'failed' && (
          <div className="flex flex-col items-center gap-2">
            <AlertCircle className={`text-red-400 ${isNormal ? 'w-10 h-10' : 'w-5 h-5'}`} />
            {isNormal && <span className="text-red-400 text-sm">Download failed</span>}
            {onRetry && (
              <button
                onClick={onRetry}
                className="flex items-center gap-1 px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 rounded-lg text-red-300 text-sm transition-colors"
              >
                <RefreshCw size={14} />
                Retry
              </button>
            )}
          </div>
        )}

        {status === 'retrying' && (
          <div className="flex flex-col items-center gap-2">
            <RefreshCw className={`text-orange-400 animate-spin ${isNormal ? 'w-8 h-8' : 'w-5 h-5'}`} />
            {isNormal && (
              <span className="text-orange-300 text-sm">
                Retrying ({retryCount}/{maxRetries})...
              </span>
            )}
          </div>
        )}

        {status === 'pending' && (
          <div className={`${isNormal ? 'w-8 h-8' : 'w-4 h-4'} rounded-full border-2 border-purple-400 border-t-transparent animate-spin`} />
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.7; }
        }
      `}</style>
    </div>
  );
};

export default NeonBorder;
