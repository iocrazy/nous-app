import { Layers, MoreVertical } from 'lucide-react';
import type { ProjectSummary } from '../../../types';

interface ProjectCardProps {
  project: ProjectSummary;
  onClick: (id: string) => void;
  onRename?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onExport?: (id: string) => void;
  onDelete?: (id: string) => void;
}

export default function ProjectCard({
  project,
  onClick,
}: ProjectCardProps) {
  return (
    <button
      type="button"
      onClick={() => onClick(project.id)}
      className="flex flex-col items-start gap-2 rounded-lg border border-gray-700 bg-gray-800/60 p-4 text-left transition-colors hover:border-gray-600 hover:bg-gray-800"
    >
      <div className="flex items-center gap-2 text-gray-300">
        <Layers size={16} />
        <span className="text-sm font-medium truncate">{project.name}</span>
      </div>
      <div className="text-xs text-gray-500">
        {new Date(project.updated_at).toLocaleDateString()}
      </div>
    </button>
  );
}
