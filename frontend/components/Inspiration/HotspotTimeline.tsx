// frontend/components/Inspiration/HotspotTimeline.tsx
// Chronological timeline view for the Hotspots tab (spec 2026-07-16). Date-grouped
// rail with a time gutter; clicking a card expands its detail inline (Variant A).
// Adapted from the legacy TopicInspiration/Timeline.tsx (which stays untouched).
import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown } from 'lucide-react';
import { HotspotCard } from '../TopicInspiration/HotspotCard';
import { HotspotCardExpand } from './HotspotCardExpand';
import type { Hotspot, HotspotStatePatch } from '../../services/topicService';

const RAIL_LEFT = 64;

function validTime(iso?: string | null): number | null {
  if (!iso) return null;
  const ms = new Date(iso).getTime();
  return Number.isNaN(ms) ? null : ms;
}

function hhmm(iso?: string | null): string {
  const ms = validTime(iso);
  if (ms === null) return '';
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function dayKeyOf(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

interface DateGroup {
  key: string;
  date: Date | null; // null = the undated bucket
  items: Hotspot[];
}

interface Props {
  hotspots: Hotspot[];
  onSaveAsNote: (h: Hotspot) => void;
  onParse: (h: Hotspot) => void;
  applyState: (h: Hotspot, patch: HotspotStatePatch) => Promise<void>;
}

export const HotspotTimeline: React.FC<Props> = ({ hotspots, onSaveAsNote, onParse, applyState }) => {
  const { t, i18n } = useTranslation();
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Sort by captured_at desc; undated items sink to the bottom. Immutable copy.
  const groups = useMemo<DateGroup[]>(() => {
    const sorted = [...hotspots].sort((a, b) => {
      const ta = validTime(a.captured_at);
      const tb = validTime(b.captured_at);
      if (ta === null && tb === null) return 0;
      if (ta === null) return 1;
      if (tb === null) return -1;
      return tb - ta;
    });
    const out: DateGroup[] = [];
    for (const h of sorted) {
      const ms = validTime(h.captured_at);
      const key = ms === null ? '__undated__' : dayKeyOf(new Date(ms));
      const last = out[out.length - 1];
      if (last && last.key === key) last.items.push(h);
      else out.push({ key, date: ms === null ? null : new Date(ms), items: [h] });
    }
    return out;
  }, [hotspots]);

  const toggleGroup = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const toggleExpand = (id: string) => setExpandedId((cur) => (cur === id ? null : id));

  const toggleSave = async (h: Hotspot) => {
    try {
      await applyState(h, { is_saved: !h.is_saved });
    } catch (err) {
      console.error('save hotspot failed', err);
    }
  };

  const hide = async (h: Hotspot) => {
    try {
      await applyState(h, { is_hidden: true });
    } catch (err) {
      console.error('hide hotspot failed', err);
    }
  };

  const groupLabel = (g: DateGroup) =>
    g.date
      ? g.date.toLocaleDateString(i18n.language, { month: 'long', day: 'numeric' })
      : t('inspiration.undated', 'Undated');

  const groupWeekday = (g: DateGroup) =>
    g.date ? g.date.toLocaleDateString(i18n.language, { weekday: 'long' }) : '';

  return (
    <div className="relative" style={{ minHeight: 420 }}>
      {/* Vertical rail: 2px, bg-line-strong, sitting at left:64px. */}
      <div className="absolute top-1.5 bottom-1.5 w-[2px] bg-line-strong" style={{ left: RAIL_LEFT }} />

      {groups.map((g) => {
        const isCollapsed = collapsed.has(g.key);
        const count = g.items.length;
        const weekday = groupWeekday(g);
        return (
          <div key={g.key} className="mb-2">
            {/* Date header: label far-left, chevron masking the rail, weekday·count
                to the right of the line (spec §2 date header layout). */}
            <button
              onClick={() => toggleGroup(g.key)}
              className="relative z-[3] block w-full text-left"
              style={{ height: 24 }}
            >
              <span
                className="absolute font-extrabold text-[14px] text-content"
                style={{ left: 0, top: '50%', transform: 'translateY(-50%)' }}
              >
                {groupLabel(g)}
              </span>
              <span
                className="absolute flex items-center justify-center rounded-full bg-island"
                style={{ left: RAIL_LEFT, top: '50%', width: 18, height: 18, transform: 'translate(-50%, -50%)' }}
              >
                <ChevronDown
                  size={13}
                  className="text-content-3 transition-transform"
                  style={{ transform: isCollapsed ? 'rotate(-90deg)' : 'none' }}
                />
              </span>
              <span
                className="absolute text-[11.5px] text-content-3"
                style={{ left: 82, top: '50%', transform: 'translateY(-50%)' }}
              >
                {weekday && `${weekday} · `}
                {t('inspiration.itemCount', '{{count}} items', { count })}
              </span>
            </button>

            {!isCollapsed &&
              g.items.map((h) => {
                const read = !!h.is_read;
                const expanded = expandedId === h.id;
                return (
                  <div key={h.id} className="relative mb-3.5 flex items-start">
                    {/* Time gutter: 64px right-aligned, pt-3.5 aligns to card top line. */}
                    <div className="w-[64px] shrink-0 pr-5 pt-3.5 text-right">
                      <span className="text-[13px] font-bold tabular-nums text-content">{hhmm(h.captured_at)}</span>
                    </div>

                    {/* Dot ON the rail; grey when read, accent otherwise. */}
                    <span
                      className={`absolute h-[13px] w-[13px] rounded-full ${read ? 'bg-content-4' : 'bg-accent'} z-[2]`}
                      style={{ left: 58, top: 15, border: '3px solid var(--island)' }}
                    />

                    <div className="ml-6 min-w-0 flex-1">
                      <HotspotCard
                        hotspot={h}
                        onSelect={() => toggleExpand(h.id)}
                        selected={expanded}
                        onToggleSave={toggleSave}
                        onToggleHide={hide}
                      >
                        {expanded && (
                          <HotspotCardExpand
                            hotspot={h}
                            onSaveAsNote={onSaveAsNote}
                            onParse={onParse}
                            onNotInterested={hide}
                          />
                        )}
                      </HotspotCard>
                    </div>
                  </div>
                );
              })}
          </div>
        );
      })}
    </div>
  );
};
