// frontend/components/DownloadProgress/WaveLiquid.tsx

import React from 'react';
import { DownloadProgressProps } from './types';
import { RefreshCw, AlertCircle, Check } from 'lucide-react';

export const WaveLiquid: React.FC<DownloadProgressProps> = ({
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
      {/* Background thumbnail */}
      {thumbnailUrl && (
        <div
          className="absolute inset-0 bg-cover bg-center"
          style={{ backgroundImage: `url(${thumbnailUrl})` }}
        >
          <div className="absolute inset-0 bg-black/40" />
        </div>
      )}

      {/* Wave container */}
      {status === 'downloading' && (
        <div
          className="absolute inset-x-0 bottom-0 transition-all duration-500 ease-out"
          style={{ height: `${percent}%` }}
        >
          {/* Wave SVG */}
          <svg
            className="absolute -top-4 left-0 w-[200%] h-8"
            style={{ animation: 'wave 3s linear infinite' }}
            viewBox="0 0 1200 120"
            preserveAspectRatio="none"
          >
            <path
              d="M0,60 C150,120 350,0 600,60 C850,120 1050,0 1200,60 L1200,120 L0,120 Z"
              fill="rgba(139, 92, 246, 0.6)"
            />
          </svg>
          <svg
            className="absolute -top-4 left-0 w-[200%] h-8"
            style={{ animation: 'wave 4s linear infinite reverse', animationDelay: '-2s' }}
            viewBox="0 0 1200 120"
            preserveAspectRatio="none"
          >
            <path
              d="M0,60 C150,120 350,0 600,60 C850,120 1050,0 1200,60 L1200,120 L0,120 Z"
              fill="rgba(99, 102, 241, 0.4)"
            />
          </svg>

          {/* Liquid body */}
          <div className="absolute inset-x-0 top-4 bottom-0 bg-gradient-to-b from-purple-500/60 to-indigo-600/80" />
        </div>
      )}

      {/* Center content */}
      <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
        {status === 'downloading' && (
          <>
            <span className={`font-bold text-white drop-shadow-lg ${isNormal ? 'text-4xl' : 'text-lg'}`}>
              {percent}%
            </span>
            {speed && isNormal && (
              <span className="text-white/80 text-sm mt-1 drop-shadow">{speed}</span>
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
        @keyframes wave {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
};

export default WaveLiquid;
