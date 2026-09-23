// frontend/components/VideoDetailPanel/ShotsTabPlaceholder.tsx
//
// The Shots tab before shot indexing exists (it arrives with PR 3). Shows
// what indexing this video would produce and a deliberately DISABLED
// trigger — the same placeholder pattern as the asset library's
// `Send To Canvas`: not a broken button, a promise with a date on it.
// Shared by VideoDetailPanel and ResourceDetailPage.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Clapperboard } from 'lucide-react';

/** Average shot length the estimate assumes, in seconds. */
const SECONDS_PER_SHOT = 4.5;
/** Each shot is embedded twice: a keyframe (Visual) and a clip (Camera). */
const EMBEDDINGS_PER_SHOT = 2;

/** ≈ number of shots in a video of `durationSeconds`; null when unknown. */
export function estimateShots(durationSeconds?: number | null): number | null {
  if (durationSeconds == null || !Number.isFinite(durationSeconds) || durationSeconds <= 0) {
    return null;
  }
  return Math.ceil(durationSeconds / SECONDS_PER_SHOT);
}

function formatClock(seconds: number): string {
  const whole = Math.floor(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export interface ShotsTabPlaceholderProps {
  durationSeconds?: number | null;
}

export const ShotsTabPlaceholder: React.FC<ShotsTabPlaceholderProps> = ({ durationSeconds }) => {
  const { t } = useTranslation();
  const shots = estimateShots(durationSeconds);
  const estimate =
    shots == null || durationSeconds == null
      ? '—'
      : t(
          'detail.shots.placeholder.estimate',
          '{{duration}} · ≈ {{shots}} shots · ≈ {{embeddings}} embeddings',
          {
            duration: formatClock(durationSeconds),
            shots,
            embeddings: shots * EMBEDDINGS_PER_SHOT,
          },
        );

  return (
    <div className="flex flex-col items-center justify-center py-12 px-4 text-center space-y-3">
      <Clapperboard size={28} className="text-content-3" />
      <h3 className="text-sm font-medium text-content">
        {t('detail.shots.placeholder.title', 'Not Indexed Yet')}
      </h3>
      <p data-testid="shots-estimate" className="text-xs text-content-2 font-mono">
        {estimate}
      </p>
      <button
        type="button"
        disabled
        title={t('detail.shots.placeholder.arrives', 'Arrives with PR 3')}
        className="px-3 py-1.5 rounded-lg text-xs font-medium border border-line-strong text-content-3 cursor-not-allowed"
      >
        {t('detail.shots.placeholder.index', 'Index This Video')}
      </button>
      <p className="text-xs text-content-3">
        {t('detail.shots.placeholder.hint', 'Runs as a Task Center task · you can keep browsing')}
      </p>
    </div>
  );
};

export default ShotsTabPlaceholder;
