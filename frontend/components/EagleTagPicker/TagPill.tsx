import React from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { Tag } from '../../types';

interface TagPillProps {
  tag: Tag;
  onRemove?: (tagId: string) => void;
  readOnly?: boolean;
  /**
   * When true, ignore the per-tag color and render a unified translucent-white
   * chip so the set reads consistently over a colored gradient backdrop.
   */
  tonal?: boolean;
}

export const TagPill: React.FC<TagPillProps> = ({ tag, onRemove, readOnly, tonal }) => {
  const { i18n } = useTranslation();
  const label = i18n.language === 'zh' && tag.name_zh ? tag.name_zh : tag.name;
  const color = tag.color || '#6366f1';

  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-full border transition-colors ${
        tonal ? 'bg-white/[0.14] text-white border-white/20' : ''
      }`}
      style={
        tonal
          ? undefined
          : {
              backgroundColor: `${color}20`,
              color: color,
              borderColor: `${color}30`,
            }
      }
    >
      {label}
      {!readOnly && onRemove && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onRemove(String(tag.id));
          }}
          className="flex items-center justify-center w-4 h-4 rounded-full hover:bg-white/20 transition-colors"
          title="Remove"
        >
          <X size={12} />
        </button>
      )}
    </span>
  );
};
