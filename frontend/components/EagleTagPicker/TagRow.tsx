import React from 'react';
import { Star } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../../types';

interface TagRowProps {
  tag: Tag;
  isSelected: boolean;
  isStarred: boolean;
  showCount: boolean;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
}

export const TagRow: React.FC<TagRowProps> = ({
  tag,
  isSelected,
  isStarred,
  showCount,
  onClick,
  onContextMenu,
}) => {
  const { i18n } = useTranslation();
  const label = i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const count = tag.media_count ?? tag.video_count ?? 0;

  return (
    <button
      onClick={onClick}
      onContextMenu={onContextMenu}
      className={`flex items-center gap-1.5 px-2 py-1 text-xs rounded transition-colors w-full min-w-0 ${
        isSelected
          ? 'bg-indigo-500/20 text-indigo-300'
          : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200'
      }`}
    >
      <span
        className="w-2.5 h-2.5 rounded-full shrink-0"
        style={{ backgroundColor: tag.color || '#6366f1' }}
      />
      <span className="truncate flex-1 text-left">{label}</span>
      {isStarred && <Star size={10} className="text-yellow-500 shrink-0 fill-yellow-500" />}
      {showCount && count > 0 && (
        <span className="text-[10px] text-zinc-600 shrink-0">({count})</span>
      )}
    </button>
  );
};
