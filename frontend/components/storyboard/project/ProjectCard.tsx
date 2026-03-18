import React, { useState, useCallback } from 'react';
import { MoreVertical, Pencil, Copy, Download, Trash2, Layers } from 'lucide-react';
import { ProjectSummary } from '../../../types';

// ─── Props ────────────────────────────────────────────────────────────────────

interface ProjectCardProps {
  project: ProjectSummary;
  onClick: (id: string) => void;
  onRename: (id: string) => void;
  onDuplicate: (id: string) => void;
  onExport: (id: string) => void;
  onDelete: (id: string) => void;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return 'Just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

const GRADIENT_COLORS = [
  'from-blue-900 to-indigo-900',
  'from-purple-900 to-pink-900',
  'from-teal-900 to-cyan-900',
  'from-orange-900 to-red-900',
];

function coverGradient(id: string | number): string {
  const s = String(id);
  const idx = s.charCodeAt(0) % GRADIENT_COLORS.length;
  return GRADIENT_COLORS[idx];
}

// ─── Component ────────────────────────────────────────────────────────────────

const ProjectCard = React.memo(function ProjectCard({
  project,
  onClick,
  onRename,
  onDuplicate,
  onExport,
  onDelete,
}: ProjectCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  const handleCardClick = useCallback(() => {
    onClick(project.id);
  }, [project.id, onClick]);

  const stopProp = useCallback((e: React.MouseEvent) => e.stopPropagation(), []);

  const menuItems = [
    { label: 'Rename', icon: <Pencil size={13} />, action: () => onRename(project.id) },
    { label: 'Duplicate', icon: <Copy size={13} />, action: () => onDuplicate(project.id) },
    { label: 'Export', icon: <Download size={13} />, action: () => onExport(project.id) },
    { label: 'Delete', icon: <Trash2 size={13} />, action: () => onDelete(project.id), danger: true },
  ];

  return (
    <div
      onClick={handleCardClick}
      className="group relative bg-gray-800 border border-gray-700 rounded-xl overflow-hidden cursor-pointer hover:border-gray-600 hover:shadow-xl transition-all"
    >
      {/* Cover image */}
      <div className={`h-36 bg-gradient-to-br ${coverGradient(project.id)} relative`}>
        {project.cover_image_url ? (
          <img
            src={project.cover_image_url}
            alt={project.name}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex items-center justify-center opacity-20">
            <Layers size={48} className="text-white" />
          </div>
        )}
      </div>

      {/* Info */}
      <div className="px-4 py-3">
        <p className="text-sm font-semibold text-gray-100 truncate" title={project.name}>
          {project.name}
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          {project.frame_count ?? 0} frames · {project.character_count ?? 0} characters
        </p>
        <p className="text-xs text-gray-600 mt-0.5">{relativeTime(project.updated_at)}</p>
      </div>

      {/* Kebab menu */}
      <div
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
        onClick={stopProp}
      >
        <button
          onClick={() => setMenuOpen((v) => !v)}
          className="p-1.5 rounded-lg bg-black/50 hover:bg-black/70 text-white transition-colors"
          title="More options"
        >
          <MoreVertical size={14} />
        </button>

        {menuOpen && (
          <div className="absolute top-full right-0 mt-1 bg-gray-900 border border-gray-700 rounded-xl shadow-xl py-1 min-w-[140px] z-30">
            {menuItems.map((item) => (
              <button
                key={item.label}
                onClick={() => { item.action(); setMenuOpen(false); }}
                className={[
                  'flex items-center gap-2 w-full px-3 py-2 text-xs transition-colors',
                  item.danger
                    ? 'text-red-400 hover:bg-red-950/50'
                    : 'text-gray-300 hover:bg-gray-800',
                ].join(' ')}
              >
                {item.icon}
                {item.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

export default ProjectCard;
