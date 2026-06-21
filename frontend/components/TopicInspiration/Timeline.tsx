// frontend/components/TopicInspiration/Timeline.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { HotspotCard } from './HotspotCard';

function hhmm(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export const Timeline: React.FC<{ hotspots: Hotspot[]; onSelect: (h: Hotspot) => void }> = ({
  hotspots,
  onSelect,
}) => {
  return (
    <div className="relative">
      <div className="absolute top-1 bottom-1 w-[2px] bg-line" style={{ left: 78 }} />
      {hotspots.map((h) => (
        <div key={h.id} className="flex items-start relative py-3.5 border-t border-line first:border-t-0">
          <div className="w-[78px] shrink-0 text-right pr-[22px] relative">
            <span className="font-bold text-[13px] text-content">{hhmm(h.captured_at)}</span>
            <span
              className="absolute top-[5px] w-[11px] h-[11px] rounded-full bg-accent border-2 border-island"
              style={{ right: -5, boxShadow: '0 0 0 2px rgba(99,102,241,.18)' }}
            />
          </div>
          <div className="flex-1 min-w-0">
            <HotspotCard hotspot={h} onSelect={onSelect} />
          </div>
        </div>
      ))}
    </div>
  );
};
