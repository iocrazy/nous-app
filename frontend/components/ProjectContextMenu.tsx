import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Settings, Users, Palette, Star, Trash2 } from 'lucide-react';
import { Project } from '../types';

const COLOR_OPTIONS = [
  { value: null, label: 'None', color: 'bg-ink-600' },
  { value: 'red', label: 'Red', color: 'bg-red-500' },
  { value: 'orange', label: 'Orange', color: 'bg-orange-500' },
  { value: 'yellow', label: 'Yellow', color: 'bg-yellow-500' },
  { value: 'green', label: 'Green', color: 'bg-green-500' },
  { value: 'blue', label: 'Blue', color: 'bg-blue-500' },
  { value: 'purple', label: 'Purple', color: 'bg-purple-500' },
  { value: 'pink', label: 'Pink', color: 'bg-pink-500' },
];

interface ProjectContextMenuProps {
  project: Project;
  position: { x: number; y: number };
  onClose: () => void;
  onSettings: () => void;
  onMembers: () => void;
  onColorLabel: (color: string | null) => void;
  onToggleStar: () => void;
  onDelete: () => void;
}

const typeColors: Record<string, string> = {
  internal: 'bg-blue-500',
  external: 'bg-orange-500',
  personal: 'bg-purple-500',
};

export const ProjectContextMenu: React.FC<ProjectContextMenuProps> = ({
  project, position, onClose, onSettings, onMembers, onColorLabel, onToggleStar, onDelete
}) => {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [showColorSub, setShowColorSub] = useState(false);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const style: React.CSSProperties = {
    position: 'fixed',
    top: Math.min(position.y, window.innerHeight - 280),
    left: Math.min(position.x, window.innerWidth - 220),
    zIndex: 50,
  };

  const MenuItem: React.FC<{
    icon: React.ReactNode; label: string; onClick: () => void;
    danger?: boolean;
  }> = ({ icon, label, onClick, danger }) => (
    <button
      onClick={() => { onClick(); onClose(); }}
      onMouseEnter={() => setShowColorSub(false)}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-md transition-colors cursor-pointer
        ${danger ? 'text-red-400 hover:bg-red-500/10' : 'text-ink-300 hover:bg-ink-700/50'}`}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div ref={ref} style={style}
      className="w-52 bg-ink-900 border border-ink-700/60 rounded-xl shadow-2xl py-1.5">
      {/* Header */}
      <div className="px-3 py-2 flex items-center gap-2 border-b border-ink-800/50 mb-1">
        <span className={`w-6 h-6 rounded-md flex items-center justify-center text-[10px]
                         font-bold text-white ${typeColors[project.project_type] || 'bg-purple-500'}`}>
          {project.name.charAt(0).toUpperCase()}
        </span>
        <span className="text-sm text-ink-200 font-medium truncate">{project.name}</span>
      </div>

      <MenuItem icon={<Settings size={14} />}
        label={t('projects.contextMenu.settings', 'Project Settings')}
        onClick={onSettings} />
      <MenuItem icon={<Users size={14} />}
        label={t('projects.contextMenu.members', 'Members')}
        onClick={onMembers} />

      <div className="border-t border-ink-800/50 my-1" />

      {/* Color Label with submenu */}
      <div className="relative"
        onMouseEnter={() => setShowColorSub(true)}
        onMouseLeave={() => setShowColorSub(false)}
      >
        <div className="w-full flex items-center gap-2.5 px-3 py-2 text-sm text-ink-300 hover:bg-ink-700/50 rounded-md cursor-default">
          <Palette size={14} />
          <span className="flex-1">{t('projects.contextMenu.colorLabel', 'Color Label')}</span>
          {project.color_label && (
            <span className={`w-3 h-3 rounded-full ${COLOR_OPTIONS.find(c => c.value === project.color_label)?.color || ''}`} />
          )}
          <span className="text-ink-600 text-xs">›</span>
        </div>
        {showColorSub && (
          <div className="absolute left-full top-0 ml-1 bg-ink-900 border border-ink-700 rounded-xl shadow-2xl py-2 px-2 w-36">
            <div className="grid grid-cols-4 gap-1.5">
              {COLOR_OPTIONS.map((opt) => (
                <button
                  key={opt.value ?? 'none'}
                  onClick={() => { onColorLabel(opt.value); onClose(); }}
                  title={opt.label}
                  className={`w-7 h-7 rounded-lg ${opt.color} transition-all hover:scale-110 ${
                    project.color_label === opt.value
                      ? 'ring-2 ring-white ring-offset-1 ring-offset-ink-900'
                      : ''
                  }`}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <MenuItem
        icon={<Star size={14} className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : ''} />}
        label={project.is_starred
          ? t('projects.contextMenu.unstar', 'Unstar')
          : t('projects.contextMenu.star', 'Star')}
        onClick={onToggleStar}
      />

      <div className="border-t border-ink-800/50 my-1" />

      <MenuItem icon={<Trash2 size={14} />}
        label={t('projects.contextMenu.delete', 'Delete Project')}
        onClick={onDelete} danger />
    </div>
  );
};
