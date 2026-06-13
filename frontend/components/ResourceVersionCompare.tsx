import React, { useState, useRef, useEffect, useCallback } from 'react';
import { X, Play, Pause, RotateCcw, Layers } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { ResourceVersion } from '../types';
import { getVersionFileUrl } from '../services/resourceService';
import { getSupabaseAccessToken } from '../supabaseClient';

interface ResourceVersionCompareProps {
  isOpen: boolean;
  onClose: () => void;
  resourceId: string;
  versionA: ResourceVersion;
  versionB: ResourceVersion;
}

export const ResourceVersionCompare: React.FC<ResourceVersionCompareProps> = ({
  isOpen,
  onClose,
  resourceId,
  versionA,
  versionB,
}) => {
  const { t } = useTranslation();
  const videoRefA = useRef<HTMLVideoElement>(null);
  const videoRefB = useRef<HTMLVideoElement>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [urlA, setUrlA] = useState<string | null>(null);
  const [urlB, setUrlB] = useState<string | null>(null);
  const [syncEnabled, setSyncEnabled] = useState(true);

  // Build authenticated URLs
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    const buildUrls = async () => {
      const token = await getSupabaseAccessToken().catch(() => null);
      if (cancelled) return;
      setUrlA(getVersionFileUrl(resourceId, versionA.id, token || undefined));
      setUrlB(getVersionFileUrl(resourceId, versionB.id, token || undefined));
    };
    buildUrls();
    return () => { cancelled = true; };
  }, [isOpen, resourceId, versionA.id, versionB.id]);

  const togglePlay = useCallback(() => {
    const a = videoRefA.current;
    const b = videoRefB.current;
    if (!a || !b) return;

    if (isPlaying) {
      a.pause();
      b.pause();
    } else {
      if (syncEnabled && b) b.currentTime = a.currentTime;
      a.play();
      b.play();
    }
    setIsPlaying(!isPlaying);
  }, [isPlaying, syncEnabled]);

  const resetBoth = useCallback(() => {
    const a = videoRefA.current;
    const b = videoRefB.current;
    if (a) { a.currentTime = 0; a.pause(); }
    if (b) { b.currentTime = 0; b.pause(); }
    setIsPlaying(false);
  }, []);

  // Sync playback: when A seeks, update B
  const handleTimeUpdateA = useCallback(() => {
    if (!syncEnabled) return;
    const a = videoRefA.current;
    const b = videoRefB.current;
    if (a && b && Math.abs(a.currentTime - b.currentTime) > 0.3) {
      b.currentTime = a.currentTime;
    }
  }, [syncEnabled]);

  // Keyboard: Space = toggle play, Escape = close
  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === ' ') {
        e.preventDefault();
        togglePlay();
      }
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, togglePlay, onClose]);

  if (!isOpen) return null;

  const isVideoA = versionA.mime_type?.startsWith('video/');
  const isVideoB = versionB.mime_type?.startsWith('video/');

  const formatSize = (bytes: number | null) => {
    if (!bytes) return '';
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-ink-950">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-ink-800 shrink-0">
        <div className="flex items-center gap-3">
          <Layers size={18} className="text-indigo-400" />
          <h2 className="text-sm font-semibold text-ink-50">
            {t('resources.compareVersions', 'Compare Versions')}
          </h2>
          <span className="text-xs text-ink-500">
            V{versionA.version_number} vs V{versionB.version_number}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isVideoA && isVideoB && (
            <>
              <button
                onClick={togglePlay}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-ink-300 hover:text-ink-50 bg-ink-800 hover:bg-ink-700 rounded-lg transition-colors"
              >
                {isPlaying ? <Pause size={14} /> : <Play size={14} />}
                <span>{isPlaying ? 'Pause' : 'Play'}</span>
              </button>
              <button
                onClick={resetBoth}
                className="p-1.5 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
                title="Reset"
              >
                <RotateCcw size={14} />
              </button>
              <label className="flex items-center gap-1.5 text-xs text-ink-400 ml-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={syncEnabled}
                  onChange={(e) => setSyncEnabled(e.target.checked)}
                  className="accent-indigo-500"
                />
                Sync
              </label>
            </>
          )}
          <button
            onClick={onClose}
            className="p-1.5 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors ml-2"
          >
            <X size={18} />
          </button>
        </div>
      </div>

      {/* Side-by-side content */}
      <div className="flex-1 flex min-h-0">
        {/* Left: Version A */}
        <div className="flex-1 flex flex-col border-r border-ink-800">
          <div className="flex items-center justify-center gap-2 px-3 py-1.5 bg-ink-900 border-b border-ink-800 shrink-0">
            <span className="px-2 py-0.5 text-xs font-semibold bg-blue-500/20 text-blue-300 rounded-full">
              V{versionA.version_number}
            </span>
            <span className="text-xs text-ink-400 truncate">{versionA.filename}</span>
            {versionA.resolution && <span className="text-[10px] text-ink-600">{versionA.resolution.replace(/:/g, 'x')}</span>}
            {versionA.file_size_bytes && <span className="text-[10px] text-ink-600">{formatSize(versionA.file_size_bytes)}</span>}
          </div>
          <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
            {isVideoA && urlA ? (
              <video
                ref={videoRefA}
                src={urlA}
                className="max-w-full max-h-full object-contain"
                onTimeUpdate={handleTimeUpdateA}
                onEnded={() => setIsPlaying(false)}
                playsInline
              />
            ) : urlA ? (
              <img src={urlA} alt={`V${versionA.version_number}`} className="max-w-full max-h-full object-contain" loading="lazy" />
            ) : (
              <p className="text-ink-500 text-sm">{t('resources.noPreview', 'Preview not available')}</p>
            )}
          </div>
        </div>

        {/* Right: Version B */}
        <div className="flex-1 flex flex-col">
          <div className="flex items-center justify-center gap-2 px-3 py-1.5 bg-ink-900 border-b border-ink-800 shrink-0">
            <span className="px-2 py-0.5 text-xs font-semibold bg-emerald-500/20 text-emerald-300 rounded-full">
              V{versionB.version_number}
            </span>
            <span className="text-xs text-ink-400 truncate">{versionB.filename}</span>
            {versionB.resolution && <span className="text-[10px] text-ink-600">{versionB.resolution.replace(/:/g, 'x')}</span>}
            {versionB.file_size_bytes && <span className="text-[10px] text-ink-600">{formatSize(versionB.file_size_bytes)}</span>}
          </div>
          <div className="flex-1 flex items-center justify-center bg-black overflow-hidden">
            {isVideoB && urlB ? (
              <video
                ref={videoRefB}
                src={urlB}
                className="max-w-full max-h-full object-contain"
                onEnded={() => setIsPlaying(false)}
                playsInline
                muted
              />
            ) : urlB ? (
              <img src={urlB} alt={`V${versionB.version_number}`} className="max-w-full max-h-full object-contain" loading="lazy" />
            ) : (
              <p className="text-ink-500 text-sm">{t('resources.noPreview', 'Preview not available')}</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
