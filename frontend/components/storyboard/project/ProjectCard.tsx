import { useState, useRef, useEffect, useCallback, memo } from 'react';
import { Layers, MoreVertical, Pencil, Copy, Download, Trash2 } from 'lucide-react';
import type { ProjectSummary } from '../../../types';

interface ProjectCardProps {
  project: ProjectSummary;
  onClick: (id: string) => void;
  onRename?: (id: string, name: string) => void;
  onDuplicate?: (id: string) => void;
  onExport?: (id: string) => void;
  onDelete?: (id: string) => void;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return `${d.getMonth() + 1}/${d.getDate()}/${d.getFullYear()}`;
}

interface MenuItem {
  key: string;
  label: string;
  icon: typeof Pencil;
  danger?: boolean;
}

const MENU_ITEMS: readonly MenuItem[] = [
  { key: 'rename', label: 'Rename', icon: Pencil },
  { key: 'duplicate', label: 'Duplicate', icon: Copy },
  { key: 'export', label: 'Export', icon: Download },
  { key: 'delete', label: 'Delete', icon: Trash2, danger: true },
];

function ProjectCard({
  project,
  onClick,
  onRename,
  onDuplicate,
  onExport,
  onDelete,
}: ProjectCardProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handleOutside = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('pointerdown', handleOutside, true);
    return () => document.removeEventListener('pointerdown', handleOutside, true);
  }, [menuOpen]);

  const handleMenuAction = useCallback(
    (action: string) => {
      setMenuOpen(false);
      switch (action) {
        case 'rename':
          onRename?.(project.id, project.name);
          break;
        case 'duplicate':
          onDuplicate?.(project.id);
          break;
        case 'export':
          onExport?.(project.id);
          break;
        case 'delete':
          onDelete?.(project.id);
          break;
      }
    },
    [project.id, project.name, onRename, onDuplicate, onExport, onDelete],
  );

  return (
    <div
      className="group relative flex items-center gap-4 rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3.5 transition-colors hover:border-zinc-600 cursor-pointer"
      onClick={() => onClick(project.id)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onClick(project.id);
      }}
    >
      {/* Icon */}
      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-600/20">
        <Layers size={20} className="text-indigo-400" />
      </div>

      {/* Content */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h3 className="truncate text-sm font-semibold text-zinc-100">
            {project.name}
          </h3>
          <span className="flex-shrink-0 rounded-full border border-teal-800/50 bg-teal-900/50 px-2 py-0.5 text-xs text-teal-400">
            Storyboard
          </span>
        </div>
        <div className="mt-1 flex items-center gap-4 text-xs text-zinc-400">
          <span>Modified: {formatDate(project.updated_at)}</span>
          <span>Created: {formatDate(project.created_at)}</span>
        </div>
      </div>

      {/* Kebab menu */}
      <div ref={menuRef} className="relative flex-shrink-0">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((prev) => !prev);
          }}
          className="flex h-8 w-8 items-center justify-center rounded-lg text-zinc-500 opacity-0 transition-all hover:bg-zinc-800 hover:text-zinc-300 group-hover:opacity-100"
        >
          <MoreVertical size={16} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-9 z-20 w-36 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-900 py-1 shadow-xl">
            {MENU_ITEMS.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  handleMenuAction(item.key);
                }}
                className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
                  item.danger
                    ? 'text-red-400 hover:bg-red-950/40'
                    : 'text-zinc-300 hover:bg-zinc-800'
                }`}
              >
                <item.icon size={12} />
                {item.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default memo(ProjectCard);
