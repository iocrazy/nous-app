import React from 'react';
import { Music } from 'lucide-react';
import { AudioWaveformPlayer } from './AudioWaveformPlayer';
import { buildSodaTheme, type SodaTheme } from '../utils/sodaTheme';

interface AudioHeroProps {
  src: string;
  /** Filename — not displayed; forwarded to the player for accessibility. */
  title?: string;
  /** Album/cover URL; falls back to a themed Music placeholder when absent. */
  coverUrl?: string;
  duration?: number;
  chorusStartSec?: number;
  onTimeUpdate?: (seconds: number) => void;
  /**
   * Track's Soda palette. When omitted, a stable per-track color is derived
   * from the src so every audio detail view (downloads / uploads / share)
   * renders with the SAME layout + a consistent color treatment.
   */
  theme?: SodaTheme;
  /**
   * Optional artist / author shown under the title on the mobile full-screen
   * player. Only rendered when present.
   */
  subtitle?: string;
}

/**
 * Shared audio detail "hero": a gradient backdrop + large rounded cover (or a
 * themed placeholder) above a centered waveform player. Used by both the
 * download detail page and the resource (upload) detail page so audio looks
 * identical everywhere.
 */
export const AudioHero: React.FC<AudioHeroProps> = ({
  src,
  title,
  coverUrl,
  duration,
  chorusStartSec,
  onTimeUpdate,
  theme,
  subtitle,
}) => {
  const t = theme ?? buildSodaTheme(null, src);
  return (
    <div
      className="w-full h-full flex flex-col items-center justify-center gap-8 sm:gap-6 px-6 py-10 sm:py-8"
      style={{ background: t.gradientCss }}
    >
      {coverUrl ? (
        <img
          src={coverUrl}
          alt={title || 'Cover'}
          className="w-64 h-64 sm:w-56 sm:h-56 rounded-2xl object-cover shadow-2xl shrink-0"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = 'none';
          }}
        />
      ) : (
        <div
          className="w-64 h-64 sm:w-56 sm:h-56 rounded-2xl flex items-center justify-center shadow-2xl shrink-0"
          style={{ backgroundColor: t.accentSoft }}
        >
          <Music size={64} style={{ color: t.onAccent }} />
        </div>
      )}
      {/* Title + artist — mobile only (full-screen player look); hidden on
          tablet/desktop where the metadata panel carries this info. */}
      {(title || subtitle) && (
        <div className="sm:hidden w-full max-w-md text-center px-2 shrink-0">
          {title && (
            <h2 className="text-xl font-bold text-white truncate" title={title}>
              {title}
            </h2>
          )}
          {subtitle && (
            <p className="mt-1 text-sm text-white/70 truncate" title={subtitle}>
              {subtitle}
            </p>
          )}
        </div>
      )}
      <div className="w-full max-w-2xl flex-1 min-h-[160px]">
        <AudioWaveformPlayer
          src={src}
          filename={title || 'Audio'}
          duration={duration}
          chorusStartSec={chorusStartSec}
          onTimeUpdate={onTimeUpdate}
          theme={t}
        />
      </div>
    </div>
  );
};
