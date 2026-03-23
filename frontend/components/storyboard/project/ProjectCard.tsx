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

function formatRelativeTime(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diffMs = now - then;
  const diffMin = Math.floor(diffMs / 60_000);

  if (diffMin < 1) return 'just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

// Generate a subtle gradient based on the project ID hash
function coverGradient(id: string): string {
  let hash = 0;
  for (let i = 0; i < id.length; i++) {
    hash = (hash * 31 + id.charCodeAt(i)) | 0;
  }
  const hue1 = Math.abs(hash % 360);
  const hue2 = (hue1 + 40) % 360;
  return `linear-gradient(135deg, hsl(${hue1}, 35%, 18%) 0%, hsl(${hue2}, 25%, 12%) 100%)`;
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

  const frameCount = project.frame_count ?? 0;
  const characterCount = project.character_count ?? 0;

  return (
    <div className="group relative overflow-hidden rounded-xl border border-zinc-700 bg-zinc-800/60 transition-colors hover:border-zinc-600 hover:bg-zinc-800">
      {/* Cover */}
      <button
        type="button"
        onClick={() => onClick(project.id)}
        className="block h-36 w-full"
        style={{ background: coverGradient(project.id) }}
      >
        <div className="flex h-full items-center justify-center">
          <Layers size={32} className="text-zinc-500/60" />
        </div>
      </button>

      {/* Info */}
      <button
        type="button"
        onClick={() => onClick(project.id)}
        className="block w-full px-4 py-3 text-left"
      >
        <h3 className="truncate text-sm font-medium text-zinc-100">{project.name}</h3>
        <div className="mt-1 flex items-center gap-3 text-[11px] text-zinc-500">
          {frameCount > 0 && <span>{frameCount} frames</span>}
          {characterCount > 0 && <span>{characterCount} chars</span>}
          <span>{formatRelativeTime(project.updated_at)}</span>
        </div>
      </button>

      {/* Kebab menu */}
      <div ref={menuRef} className="absolute right-2 top-2">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setMenuOpen((prev) => !prev);
          }}
          className="flex h-7 w-7 items-center justify-center rounded-lg bg-black/40 text-zinc-300 opacity-0 transition-all hover:bg-black/60 group-hover:opacity-100"
        >
          <MoreVertical size={14} />
        </button>

        {menuOpen && (
          <div className="absolute right-0 top-8 z-20 w-36 overflow-hidden rounded-lg border border-zinc-700 bg-zinc-900 py-1 shadow-xl">
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
