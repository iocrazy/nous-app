// frontend/components/TopicInspiration/Timeline.tsx
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { Hotspot } from '../../services/topicService';
import { HotspotCard } from './HotspotCard';

function hhmm(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function dayKeyOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`;
}

function dayKey(iso?: string | null): string {
  if (!iso) return 'unknown';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 'unknown' : dayKeyOf(d);
}

function dayLabel(key: string, t: (k: string, f?: string) => string): string {
  if (key === 'unknown') return t('topic.undated', 'Undated');
  const now = new Date();
  const yesterday = new Date();
  yesterday.setDate(now.getDate() - 1);
  if (key === dayKeyOf(now)) return t('topic.today', 'Today');
  if (key === dayKeyOf(yesterday)) return t('topic.yesterday', 'Yesterday');
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

interface Props {
  hotspots: Hotspot[];
  onSelect: (h: Hotspot) => void;
  selectedId?: string;
  onToggleSave?: (h: Hotspot) => void;
  onToggleHide?: (h: Hotspot) => void;
  /** Group items under collapsible date headers (chronological views). Set
   *  false for rank-ordered views (Featured, For You) where day headers would
   *  jumble — items render flat in the given order. Default true. */
  grouped?: boolean;
}

export const Timeline: React.FC<Props> = ({
  hotspots,
  onSelect,
  selectedId,
  onToggleSave,
  onToggleHide,
  grouped = true,
}) => {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  // Group hotspots into consecutive day buckets so the timeline shows which
  // day each run of items belongs to (the feed can span multiple days). For
  // rank-ordered views one synthetic bucket (no header) renders items flat.
  const groups = useMemo(() => {
    if (!grouped) return [{ key: '__flat__', items: hotspots }];
    const out: { key: string; items: Hotspot[] }[] = [];
    for (const h of hotspots) {
      const k = dayKey(h.captured_at);
      const last = out[out.length - 1];
      if (last && last.key === k) last.items.push(h);
      else out.push({ key: k, items: [h] });
    }
    return out;
  }, [hotspots, grouped]);

  const toggle = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="relative">
      {/* Vertical line: 2px, bg-line-strong, sitting at left:64px */}
      <div className="absolute top-1.5 bottom-1.5 w-[2px] bg-line-strong" style={{ left: 64 }} />

      {groups.map((g) => {
        const isCollapsed = collapsed.has(g.key);
        return (
          <div key={g.key}>
            {/* Date section header — sits above the line, spans full width.
                Hidden for the synthetic flat bucket (rank-ordered views). */}
            {grouped && (
              <button
                onClick={() => toggle(g.key)}
                className="relative z-[3] flex items-center gap-1 mb-2 mt-1 ml-[6px] text-[12px] font-bold text-content-2 hover:text-content"
              >
                {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                <span>{dayLabel(g.key, t)}</span>
                <span className="text-[11px] font-normal text-content-4">· {g.items.length}</span>
              </button>
            )}

            {!isCollapsed &&
              g.items.map((h) => (
                <div key={h.id} className="flex items-start relative mb-3.5">
                  {/* Time rail: 64px, right-aligned, pt-3.5 to align with card top */}
                  <div className="w-[64px] shrink-0 text-right pr-5 pt-3.5">
                    <span className="font-bold text-[13px] text-content">{hhmm(h.captured_at)}</span>
                  </div>

                  {/* Dot ON the line: 13px circle, bg-accent, 3px border in island bg */}
                  <span
                    className="absolute w-[13px] h-[13px] rounded-full bg-accent z-[2]"
                    style={{ left: 58, top: 20, border: '3px solid var(--island, #fff)' }}
                  />

                  {/* Card: flex-1, ml-6 (24px) to the right of the line */}
                  <div className="flex-1 min-w-0 ml-6">
                    <HotspotCard
                      hotspot={h}
                      onSelect={onSelect}
                      selected={h.id === selectedId}
                      onToggleSave={onToggleSave}
                      onToggleHide={onToggleHide}
                    />
                  </div>
                </div>
              ))}
          </div>
        );
      })}
    </div>
  );
};
