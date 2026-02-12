import React from 'react';
import { Star, Clock, FileText } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project } from '../types';

interface ProjectCardProps {
  project: Project;
  onClick: () => void;
  onToggleStar: (e: React.MouseEvent) => void;
}

const typeColors: Record<string, { border: string; badge: string; text: string }> = {
  internal: { border: 'border-l-blue-500', badge: 'bg-blue-500/20 text-blue-400', text: 'Internal' },
  external: { border: 'border-l-orange-500', badge: 'bg-orange-500/20 text-orange-400', text: 'External' },
  personal: { border: 'border-l-purple-500', badge: 'bg-purple-500/20 text-purple-400', text: 'Personal' },
};

const formatRelativeTime = (dateStr: string): string => {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMinutes = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMinutes / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString();
};

export const ProjectCard: React.FC<ProjectCardProps> = ({ project, onClick, onToggleStar }) => {
  const { t } = useTranslation();
  const colors = typeColors[project.project_type] || typeColors.personal;

  return (
    <div
      onClick={onClick}
      className={`bg-zinc-800/80 hover:bg-zinc-800 border border-zinc-700/50 hover:border-zinc-600 rounded-xl p-5 cursor-pointer transition-all duration-200 group border-l-4 ${colors.border}`}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <h3 className="text-white font-medium text-base truncate group-hover:text-indigo-300 transition-colors">
            {project.name}
          </h3>
          {project.description && (
            <p className="text-zinc-400 text-sm mt-1 line-clamp-2">{project.description}</p>
          )}
        </div>
        <button
          onClick={onToggleStar}
          className="ml-2 p-1.5 rounded-lg hover:bg-zinc-700 transition-colors flex-shrink-0"
        >
          <Star
            size={16}
            className={project.is_starred ? 'text-yellow-400 fill-yellow-400' : 'text-zinc-500 hover:text-yellow-400'}
          />
        </button>
      </div>

      <div className="flex items-center gap-3 mt-4">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${colors.badge}`}>
          {t(`mediatrack.${project.project_type}`) || colors.text}
        </span>
        {project.project_group && (
          <span className="text-xs text-zinc-500 bg-zinc-700/50 px-2 py-0.5 rounded-full">
            {project.project_group}
          </span>
        )}
      </div>

      <div className="flex items-center justify-between mt-4 text-xs text-zinc-500">
        <div className="flex items-center gap-1">
          <FileText size={12} />
          <span>{project.file_count} {t('mediatrack.files')}</span>
        </div>
        <div className="flex items-center gap-1">
          <Clock size={12} />
          <span>{formatRelativeTime(project.updated_at)}</span>
        </div>
      </div>
    </div>
  );
};
