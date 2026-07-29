import React from 'react';

// Speaker-diarization chip colors — deterministic per speaker label so the
// SAME speaker always gets the SAME color within a transcript (S01 stays
// indigo, S02 stays emerald, ...). Purely presentational.
export const SPEAKER_CHIP_CLASSES = [
  'bg-indigo-500/15 text-indigo-300 border-indigo-500/30',
  'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  'bg-amber-500/15 text-warn border-amber-500/30',
  'bg-sky-500/15 text-sky-300 border-sky-500/30',
  'bg-rose-500/15 text-rose-300 border-rose-500/30',
  'bg-violet-500/15 text-violet-300 border-violet-500/30',
  'bg-teal-500/15 text-teal-300 border-teal-500/30',
  'bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30',
];

/** Map a speaker label to a stable chip color class. Same input → same output. */
export const speakerChipClass = (speaker: string): string => {
  let hash = 0;
  for (let i = 0; i < speaker.length; i++) {
    hash = (hash * 31 + speaker.charCodeAt(i)) >>> 0;
  }
  return SPEAKER_CHIP_CLASSES[hash % SPEAKER_CHIP_CLASSES.length];
};

interface SpeakerChipProps {
  /** Speaker label (e.g. "S01"). Renders nothing when absent — old / non-
   *  diarized transcripts (OpenAI Whisper, Volcengine) pass undefined. */
  speaker?: string | null;
}

/** Small colored chip labeling which speaker a transcript segment belongs to.
 *  Renders null (zero regression) when the segment has no speaker. */
export const SpeakerChip: React.FC<SpeakerChipProps> = ({ speaker }) => {
  if (!speaker) return null;
  return (
    <span
      data-testid="speaker-chip"
      data-speaker={speaker}
      className={`shrink-0 self-start mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold font-mono border ${speakerChipClass(speaker)}`}
      title={`Speaker ${speaker}`}
    >
      {speaker}
    </span>
  );
};
