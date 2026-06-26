/**
 * ChatSidebar — channel list panel for the Team Chat feature.
 *
 * Renders two sections based on channel.type:
 *   - Groups   → type === 'group' | 'public'
 *   - Direct Messages → type === 'dm'
 *
 * Purely presentational — all state lives in the parent.
 */

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Search, Hash, Lock } from 'lucide-react';
import type { Channel } from '../../types';

export interface ChatSidebarProps {
  channels: Channel[];
  activeId: string;
  onSelect: (id: string) => void;
  onNew?: () => void;
}

function ChannelRow({
  channel,
  isActive,
  onSelect,
}: {
  channel: Channel;
  isActive: boolean;
  onSelect: (id: string) => void;
}): React.ReactElement {
  const { t } = useTranslation();
  const label = channel.name ?? channel.id;

  const activeClass = isActive
    ? 'bg-indigo-500/[0.18] text-indigo-300 font-[550]'
    : 'text-[#a3a3ad] hover:bg-[#17171b] hover:text-[#e7e7ea]';

  const glyphActiveClass = isActive ? 'text-indigo-300' : 'text-[#74747e]';

  const glyph =
    channel.type === 'public' ? (
      <Hash size={14} className={glyphActiveClass} />
    ) : channel.type === 'group' ? (
      <Lock size={13} className={glyphActiveClass} />
    ) : null;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(channel.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onSelect(channel.id);
      }}
      className={[
        'flex items-center gap-[9px] px-2 py-[7px] rounded-[9px] cursor-pointer mb-px transition-colors',
        activeClass,
      ].join(' ')}
    >
      {/* Glyph / Avatar */}
      {channel.type === 'dm' ? (
        <span className="w-[18px] h-[18px] rounded-full flex-shrink-0 bg-gradient-to-br from-slate-500 to-slate-400 text-[9px] grid place-items-center text-white font-semibold">
          {label.slice(0, 2).toUpperCase()}
        </span>
      ) : (
        <span className="w-[18px] flex-shrink-0 grid place-items-center">
          {glyph}
        </span>
      )}

      {/* Name */}
      <span className="flex-1 text-[13.5px] whitespace-nowrap overflow-hidden text-ellipsis">
        {label}
      </span>

      {/* Unread badge */}
      {channel.unread > 0 && (
        <span className="text-[10.5px] font-[650] min-w-[18px] h-[18px] px-[5px] rounded-[9px] grid place-items-center bg-indigo-500 text-white">
          {channel.unread}
        </span>
      )}

      {/* Mention badge — amber pill, visually distinct from the neutral unread dot */}
      {channel.mentions > 0 && (
        <span
          className="text-[10.5px] font-[650] min-w-[18px] h-[18px] px-[5px] rounded-[9px] grid place-items-center bg-amber-400/15 text-amber-400"
          title={t('chat.mentionsBadgeTitle', { count: channel.mentions })}
        >
          @{channel.mentions}
        </span>
      )}
    </div>
  );
}

export function ChatSidebar({
  channels,
  activeId,
  onSelect,
  onNew,
}: ChatSidebarProps): React.ReactElement {
  const { t } = useTranslation();

  const groups = channels.filter(
    (c) => c.type === 'group' || c.type === 'public',
  );
  const dms = channels.filter((c) => c.type === 'dm');

  return (
    <div className="flex flex-col h-full overflow-hidden bg-[#15151a] border border-white/[.12] rounded-[18px]">
      {/* Header */}
      <div className="px-[14px] pt-[14px] pb-[10px] flex items-center justify-between flex-shrink-0">
        <h2 className="text-[15px] font-[650] tracking-[-0.01em] text-[#e7e7ea]">
          {t('chat.sidebarTitle')}
        </h2>
        <button
          type="button"
          title={t('chat.newChannel')}
          onClick={onNew}
          className="w-[26px] h-[26px] rounded-[7px] grid place-items-center text-[#74747e] hover:text-[#e7e7ea] hover:bg-[#17171b] border border-transparent transition-colors"
        >
          <Plus size={14} />
        </button>
      </div>

      {/* Search */}
      <div className="mx-3 mb-[10px] flex items-center gap-2 py-[7px] px-[10px] bg-ink-950 border border-white/[.065] rounded-[9px] text-[#74747e] flex-shrink-0">
        <Search size={13} className="flex-shrink-0" />
        <input
          type="text"
          placeholder={t('chat.searchPlaceholder')}
          className="flex-1 bg-transparent border-none outline-none text-[#e7e7ea] text-[13px] placeholder:text-[#74747e] font-[inherit]"
        />
      </div>

      {/* Scrollable channel list */}
      <div className="flex-1 overflow-y-auto px-2 pb-3 scrollbar-thin scrollbar-thumb-white/[.08]">
        {/* Groups section */}
        {groups.length > 0 && (
          <>
            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[#6b6b75] pt-3 pb-[5px] px-[6px]">
              {t('chat.sectionGroups')}
            </div>
            {groups.map((ch) => (
              <ChannelRow
                key={ch.id}
                channel={ch}
                isActive={ch.id === activeId}
                onSelect={onSelect}
              />
            ))}
          </>
        )}

        {/* Direct Messages section */}
        {dms.length > 0 && (
          <>
            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-[#6b6b75] pt-3 pb-[5px] px-[6px]">
              {t('chat.sectionDMs')}
            </div>
            {dms.map((ch) => (
              <ChannelRow
                key={ch.id}
                channel={ch}
                isActive={ch.id === activeId}
                onSelect={onSelect}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}
