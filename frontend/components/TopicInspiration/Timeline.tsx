// frontend/components/TopicInspiration/Timeline.tsx
import React from 'react';
import type { Hotspot } from '../../services/topicService';
import { HotspotCard } from './HotspotCard';

function hhmm(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

interface Props {
  hotspots: Hotspot[];
  onSelect: (h: Hotspot) => void;
  selectedId?: string;
}

export const Timeline: React.FC<Props> = ({ hotspots, onSelect, selectedId }) => {
  return (
    <div className="relative">
      {/* Vertical line: 2px, bg-line-strong, sitting at left:64px */}
      <div
        className="absolute top-1.5 bottom-1.5 w-[2px] bg-line-strong"
        style={{ left: 64 }}
      />

      {hotspots.map((h) => (
        <div key={h.id} className="flex items-start relative mb-3.5">
          {/* Time rail: 64px, right-aligned text, pt-3.5 to vertically align with card top */}
          <div className="w-[64px] shrink-0 text-right pr-5 pt-3.5">
            <span className="font-bold text-[13px] text-content">
              {hhmm(h.captured_at)}
            </span>
          </div>

          {/* Dot ON the line: 13px circle, bg-accent, 3px border in island bg color, z-2 */}
          <span
            className="absolute w-[13px] h-[13px] rounded-full bg-accent z-[2]"
            style={{
              left: 58,
              top: 20,
              border: '3px solid var(--island, #fff)',
            }}
          />

          {/* Card: flex-1, ml-6 (24px) to the right of the line */}
          <div className="flex-1 min-w-0 ml-6">
            <HotspotCard
              hotspot={h}
              onSelect={onSelect}
              selected={h.id === selectedId}
            />
          </div>
        </div>
      ))}
    </div>
  );
};
