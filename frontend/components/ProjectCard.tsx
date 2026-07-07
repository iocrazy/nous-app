import React from 'react';
import { Star, Clock, FileText, MoreVertical } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Project } from '../types';
import { formatRelativeTime } from '../utils/relativeTime';
import { StageRing } from './project/StageRing';

interface ProjectCardProps {
  project: Project;
  onClick: () => void;
  onToggleStar: (e: React.MouseEvent) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}

const typeColors: Record<string, { border: string; badge: string; text: string }> = {
  internal: { border: 'border-l-blue-500', badge: 'bg-blue-500/20 text-blue-400', text: 'Internal' },
  external: { border: 'border-l-orange-500', badge: 'bg-orange-500/20 text-orange-400', text: 'External' },
  personal: { border: 'border-l-purple-500', badge: 'bg-purple-500/20 text-purple-400', text: 'Personal' },
};

const colorLabelBorders: Record<string, string> = {
  red: 'border-l-red-500',
  orange: 'border-l-orange-500',
  yellow: 'border-l-yellow-500',
  green: 'border-l-green-500',
  blue: 'border-l-blue-500',
  purple: 'border-l-purple-500',
  pink: 'border-l-pink-500',
};

// Deterministic avatar hue per user id, so initials chips stay stable
// across renders without storing a color anywhere.
const AVATAR_HUES = [212, 32, 152, 262, 105, 342];
function avatarHue(userId: string): number {
  let h = 0;
  for (let i = 0; i < userId.length; i++) h = (h * 31 + userId.charCodeAt(i)) >>> 0;
  return AVATAR_HUES[h % AVATAR_HUES.length];
}

function initials(username: string, userId: string): string {
  const src = (username || userId).trim();
  return src.slice(0, 2).toUpperCase();
}

/**
 * Project card (Phase B B1 — Stage Ring layout): segmented stage ring is the
 * primary visual, followed by the latest stage activity, then stage chip +
 * member stack + file count. Every enrichment field is optional — a project
 * with no stage/members/history renders the base card unchanged.
 */
export const ProjectCard: React.FC<ProjectCardProps> = ({
  project,
  onClick,
  onToggleStar,
  onContextMenu,
}) => {
  const { t } = useTranslation();
  const colors = typeColors[project.project_type] || typeColors.personal;
  const borderColor = project.color_label
    ? colorLabelBorders[project.color_label] || colors.border
    : colors.border;
  const isArchived = Boolean(project.archived_at);
  const stage = project.current_stage ?? null;
  const membersPreview = project.members_preview ?? null;
  const activity = project.latest_activity ?? null;

  return (
    <div
      onClick={onClick}
      className={`bg-ink-800/80 hover:bg-ink-800 border border-ink-700/50 hover:border-ink-600 rounded-xl p-5 cursor-pointer transition-all duration-200 group border-l-4 ${borderColor} ${
        isArchived ? 'opacity-55' : ''
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <StageRing stage={isArchived ? null : stage} />
          <div className="min-w-0">
            <h3 className="text-ink-50 font-medium text-base truncate group-hover:text-indigo-300 transition-colors">
              {project.name}
            </h3>
            {project.description && (
              <p className="text-ink-400 text-sm mt-0.5 line-clamp-1">{project.description}</p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            onClick={onToggleStar}
            className="p-1.5 rounded-lg hover:bg-ink-700 transition-colors"
          >
            <Star
              size={16}
              className={
                project.is_starred
                  ? 'text-yellow-400 fill-yellow-400'
                  : 'text-ink-500 hover:text-yellow-400'
              }
            />
          </button>
          {onContextMenu && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onContextMenu(e);
              }}
              data-testid="project-menu-btn"
              className="p-1.5 rounded-lg hover:bg-ink-700 text-ink-500 hover:text-ink-200
                         transition-colors opacity-0 group-hover:opacity-100"
            >
              <MoreVertical size={16} />
            </button>
          )}
        </div>
      </div>

      <div className="flex items-center gap-1.5 mt-3 text-xs text-ink-400 min-w-0">
        {activity ? (
          <>
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 flex-shrink-0" />
            <span className="truncate">
              {t('projects.card.enteredStage', { stage: activity.stage_name })}
              {activity.actor ? ` · ${activity.actor}` : ''}
              {' · '}
              {formatRelativeTime(activity.entered_at, t)}
            </span>
          </>
        ) : (
          <>
            <Clock size={12} className="flex-shrink-0" />
            <span>{formatRelativeTime(project.updated_at, t)}</span>
          </>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 mt-4 text-xs">
        <div className="flex items-center gap-2 min-w-0">
          {isArchived ? (
            <span className="px-2 py-0.5 rounded-full font-medium bg-ink-700/50 text-ink-400">
              {t('projects.card.archived')}
            </span>
          ) : stage ? (
            <span className="px-2 py-0.5 rounded-full font-medium bg-indigo-500/15 text-indigo-300">
              {stage.name}
            </span>
          ) : (
            <span className={`px-2 py-0.5 rounded-full font-medium ${colors.badge}`}>
              {t(`mediatrack.${project.project_type}`, colors.text)}
            </span>
          )}
          {project.project_group && (
            <span className="text-ink-500 bg-ink-700/50 px-2 py-0.5 rounded-full truncate">
              {project.project_group}
            </span>
          )}
        </div>

        <div className="flex items-center gap-3 flex-shrink-0 text-ink-500">
          {membersPreview && membersPreview.count > 0 && (
            <span className="flex items-center" data-testid="member-stack">
              {membersPreview.members.map((m, i) => (
                <span
                  key={m.user_id}
                  title={m.username || m.user_id}
                  className={`w-[22px] h-[22px] rounded-full border-2 border-ink-800 grid place-items-center
                              text-[9px] font-bold text-ink-950 ${i > 0 ? '-ml-1.5' : ''}`}
                  style={{ backgroundColor: `hsl(${avatarHue(m.user_id)} 30% 65%)` }}
                >
                  {initials(m.username, m.user_id)}
                </span>
              ))}
              {membersPreview.count > membersPreview.members.length && (
                <span
                  className="w-[22px] h-[22px] rounded-full border-2 border-ink-800 grid place-items-center
                             text-[9px] font-bold bg-ink-700 text-ink-300 -ml-1.5"
                >
                  +{membersPreview.count - membersPreview.members.length}
                </span>
              )}
            </span>
          )}
          <span className="flex items-center gap-1">
            <FileText size={12} />
            <span>
              {project.file_count} {t('mediatrack.files')}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
};
