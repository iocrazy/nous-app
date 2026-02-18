import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Settings, Users, Bookmark, Star, Trash2 } from 'lucide-react';
import { Project } from '../types';

interface ProjectContextMenuProps {
  project: Project;
  position: { x: number; y: number };
  onClose: () => void;
  onSettings: () => void;
  onMembers: () => void;
  onToggleStar: () => void;
  onDelete: () => void;
}

const typeColors: Record<string, string> = {
  internal: 'bg-blue-500',
  external: 'bg-orange-500',
  personal: 'bg-purple-500',
};

export const ProjectContextMenu: React.FC<ProjectContextMenuProps> = ({
  project, position, onClose, onSettings, onMembers, onToggleStar, onDelete
}) => {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

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
    danger?: boolean; disabled?: boolean;
  }> = ({ icon, label, onClick, danger, disabled }) => (
    <button
      onClick={() => { onClick(); onClose(); }}
      disabled={disabled}
      className={`w-full flex items-center gap-2.5 px-3 py-2 text-sm rounded-md transition-colors
        ${danger ? 'text-red-400 hover:bg-red-500/10' : 'text-zinc-300 hover:bg-zinc-700/50'}
        ${disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div ref={ref} style={style}
      className="w-52 bg-zinc-900 border border-zinc-700/60 rounded-xl shadow-2xl py-1.5">
      {/* Header */}
      <div className="px-3 py-2 flex items-center gap-2 border-b border-zinc-800/50 mb-1">
        <span className={`w-6 h-6 rounded-md flex items-center justify-center text-[10px]
                         font-bold text-white ${typeColors[project.project_type] || 'bg-purple-500'}`}>
          {project.name.charAt(0).toUpperCase()}
        </span>
        <span className="text-sm text-zinc-200 font-medium truncate">{project.name}</span>
      </div>

      <MenuItem icon={<Settings size={14} />}
        label={t('projects.contextMenu.settings', 'Project Settings')}
        onClick={onSettings} />
      <MenuItem icon={<Users size={14} />}
        label={t('projects.contextMenu.members', 'Members')}
        onClick={onMembers} />

      <div className="border-t border-zinc-800/50 my-1" />

      <MenuItem icon={<Bookmark size={14} />}
        label={t('projects.contextMenu.colorLabel', 'Color Label')}
        onClick={() => {}} disabled />
      <MenuItem
        icon={<Star size={14} className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : ''} />}
        label={project.is_starred
          ? t('projects.contextMenu.unstar', 'Unstar')
          : t('projects.contextMenu.star', 'Star')}
        onClick={onToggleStar}
      />

      <div className="border-t border-zinc-800/50 my-1" />

      <MenuItem icon={<Trash2 size={14} />}
        label={t('projects.contextMenu.delete', 'Delete Project')}
        onClick={onDelete} danger />
    </div>
  );
};
