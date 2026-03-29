import { useState, useRef, useEffect, useCallback, memo } from 'react';
import { Layers, Pencil, Trash2 } from 'lucide-react';
import type { ProjectSummary } from '../../../types';

interface ProjectCardProps {
  project: ProjectSummary;
  selectMode?: boolean;
  onClick: (id: string) => void;
  onRename?: (id: string, name: string) => void;
  onDelete?: (id: string) => void;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear()}`;
}

function ProjectCard({
  project,
  selectMode,
  onClick,
  onRename,
  onDelete,
}: ProjectCardProps) {
  const [selected, setSelected] = useState(false);

  return (
    <div
      className="group relative flex flex-col rounded-xl border border-zinc-800 bg-zinc-900 p-4 transition-colors hover:border-zinc-600 cursor-pointer"
      onClick={() => selectMode ? setSelected(v => !v) : onClick(project.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick(project.id);
      }}
    >
      {/* Top row: icon + title + actions */}
      <div className="flex items-start gap-3">
        <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-600/20">
          <Layers size={18} className="text-indigo-400" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-zinc-100">{project.name}</h3>
        </div>
        {/* Actions or checkbox */}
        {selectMode ? (
          <div
            className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${
              selected ? 'border-indigo-500 bg-indigo-600' : 'border-zinc-600 bg-zinc-800'
            }`}
          >
            {selected && (
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
            )}
          </div>
        ) : (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRename?.(project.id, project.name); }}
              className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-800 hover:text-zinc-300 transition-colors"
              title="Rename"
            >
              <Pencil size={14} />
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onDelete?.(project.id); }}
              className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-red-950/50 hover:text-red-400 transition-colors"
              title="Delete"
            >
              <Trash2 size={14} />
            </button>
          </div>
        )}
      </div>

      {/* Tag badge */}
      <div className="mt-2.5">
        <span className="rounded-full border border-teal-800/50 bg-teal-900/40 px-2 py-0.5 text-[11px] text-teal-400">
          Storyboard
        </span>
      </div>

      {/* Dates */}
      <div className="mt-2 space-y-0.5 text-xs text-zinc-500">
        <p>Modified: <span className="text-zinc-400">{formatDate(project.updated_at)}</span></p>
        <p>Created: <span className="text-zinc-400">{formatDate(project.created_at)}</span></p>
      </div>
    </div>
  );
}

export default memo(ProjectCard);
