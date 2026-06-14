import React from 'react';
import { CometBack } from './CometBack';

interface AudioStageIslandProps {
  title?: string;
  author?: string;
  onBack: () => void;
  /** Share / Download / More buttons (built by the page). */
  actions: React.ReactNode;
  /** Cover-side Overview column — the page passes <MediaCard .../> (audio). */
  coverSide: React.ReactNode;
  /** Lyrics column — the page passes <LyricsView variant="bare" .../>. */
  lyrics: React.ReactNode;
  /** Bottom play capsule contents — the page passes <AudioWaveformPlayer .../>. */
  player: React.ReactNode;
}

/**
 * Island redesign v2 P4 — cover-tinted audio stage (spec §5.3). Pure layout:
 * the page builds the real MediaCard / LyricsView / AudioWaveformPlayer children
 * (so they behave identically — D12) and hands them in as slots. The tint backdrop
 * reads --tint / --tint-deep, set on this root by the consumer.
 * Desktop-only (the island frame is `hidden sm:flex`).
 */
export const AudioStageIsland: React.FC<AudioStageIslandProps> = ({ title, author, onBack, actions, coverSide, lyrics, player }) => (
  <div className="audio-stage hidden sm:flex flex-col h-full min-h-0">
    <div className="stage-head flex items-center gap-3 px-4 py-3 relative z-[2]">
      <CometBack onClick={onBack} tinted />
      <div className="min-w-0">
        <div className="text-[13.5px] font-semibold text-ink-50 truncate">{title}</div>
        {author && <div className="text-[11.5px] text-ink-400 truncate">{author}</div>}
      </div>
      <span className="comet-trail" />
      <div className="ml-auto flex items-center gap-2">{actions}</div>
    </div>
    <div className="flex-1 min-h-0 flex items-start justify-center gap-8 px-6 py-4 overflow-auto relative z-[2]">
      <div className="w-[min(440px,42%)] shrink-0">{coverSide}</div>
      <div className="lyrics-col flex-1 max-w-[440px] self-stretch min-h-0 overflow-hidden">{lyrics}</div>
    </div>
    <div className="audio-capsule shrink-0 relative z-[2]">{player}</div>
  </div>
);
