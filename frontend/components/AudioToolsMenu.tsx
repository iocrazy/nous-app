import React from 'react';

interface AudioToolsMenuItemsProps {
  /** Resource notes — editable inline when a setter is provided. */
  resourceNotes?: string;
  onNotesChange?: (notes: string) => void;
  onNotesBlur?: () => void;
}

/**
 * Audio More-menu extras for the island audio stage. Per user direction the
 * audio detail does NOT surface the AI actions (Copy / Transcript / Summary /
 * Analyze) — audio doesn't need them — so only inline Notes editing remains,
 * folded into the shared stage-head More ("…") menu (audio-only). Rendered as
 * bare items so it sits seamlessly with the surrounding menu entries.
 */
export const AudioToolsMenuItems: React.FC<AudioToolsMenuItemsProps> = ({
  resourceNotes,
  onNotesChange,
  onNotesBlur,
}) => {
  if (!onNotesChange) return null;
  return (
    <div className="px-3 pt-1.5 pb-2">
      <label className="block text-[10px] font-semibold text-content-3 uppercase tracking-wider mb-1">Notes</label>
      <textarea
        value={resourceNotes || ''}
        onChange={(e) => onNotesChange(e.target.value)}
        onBlur={onNotesBlur}
        placeholder="Add notes..."
        rows={2}
        className="w-full bg-island-2 border border-line rounded-lg px-2 py-1.5 text-xs text-content-2 placeholder-content-4 focus:outline-none focus:border-indigo-500/50 resize-none"
      />
    </div>
  );
};
