import { useState, useEffect, useRef, useCallback } from 'react';
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ArrowLeft,
  FolderOpen,
  FileText,
  Clapperboard,
  Download,
  KanbanSquare,
  Share2,
  Trash2,
  Settings,
  Search,
  Star,
  Clock,
  LayoutGrid,
  Calendar,
  Tag,
  Wand2,
} from 'lucide-react';
import { Project } from '../../types';

interface ProjectNavSidebarProps {
  project: Project;
  activeSection: string;
  onSectionChange: (section: string) => void;
  onBackToList: () => void;
  recentProjects: Project[];
  starredProjects: Project[];
  onProjectSwitch: (project: Project) => void;
  sectionCounts?: Record<string, number>;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

const AVATAR_COLORS = [
  'bg-rose-500', 'bg-amber-500', 'bg-emerald-500', 'bg-cyan-500',
  'bg-blue-500', 'bg-indigo-500', 'bg-violet-500', 'bg-pink-500',
] as const;

const NAV_SECTIONS = [
  { key: 'files', label: 'Files', icon: FolderOpen, iconColor: '' },
  { key: 'scripts', label: 'Scripts', icon: FileText, iconColor: '' },
  { key: 'storyboard', label: 'Storyboard', icon: Clapperboard, iconColor: '' },
  { key: 'skills', label: 'Skills', icon: Wand2, iconColor: 'text-violet-400' },
  { key: 'output', label: 'Output', icon: Download, iconColor: '' },
  { key: 'tasks', label: 'Tasks', icon: KanbanSquare, iconColor: '' },
  { key: 'divider-1', label: '', icon: null, iconColor: '' },
  { key: 'shares', label: 'Shares', icon: Share2, iconColor: '' },
  { key: 'trash', label: 'Trash', icon: Trash2, iconColor: '' },
  { key: 'divider-2', label: '', icon: null, iconColor: '' },
  { key: 'settings', label: 'Settings', icon: Settings, iconColor: '' },
] as const;

function getAvatarColor(name: string): string {
  const code = name.charCodeAt(0) || 0;
  return AVATAR_COLORS[code % AVATAR_COLORS.length];
}

function ProjectAvatar({ name, size = 'sm' }: { name: string; size?: 'sm' | 'md' }) {
  const letter = (name[0] || '?').toUpperCase();
  const color = getAvatarColor(name);
  const sizeClass = size === 'md' ? 'w-8 h-8 text-sm' : 'w-6 h-6 text-xs';
  return (
    <div className={`${sizeClass} ${color} rounded-md flex items-center justify-center text-white font-semibold shrink-0`}>
      {letter}
    </div>
  );
}

export function ProjectNavSidebar({
  project,
  activeSection,
  onSectionChange,
  onBackToList,
  recentProjects,
  starredProjects,
  onProjectSwitch,
  sectionCounts = {},
  collapsed = false,
  onToggleCollapse,
}: ProjectNavSidebarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  const handleToggleDropdown = useCallback(() => {
    setDropdownOpen(prev => !prev);
    setSearchQuery('');
  }, []);

  const handleCloseDropdown = useCallback(() => {
    setDropdownOpen(false);
    setSearchQuery('');
  }, []);

  const handleProjectClick = useCallback((p: Project) => {
    handleCloseDropdown();
    onProjectSwitch(p);
  }, [onProjectSwitch, handleCloseDropdown]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        handleCloseDropdown();
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [dropdownOpen, handleCloseDropdown]);

  // Close on Escape
  useEffect(() => {
    if (!dropdownOpen) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleCloseDropdown();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [dropdownOpen, handleCloseDropdown]);

  // Focus search on open
  useEffect(() => {
    if (dropdownOpen) {
      requestAnimationFrame(() => searchInputRef.current?.focus());
    }
  }, [dropdownOpen]);

  const filterProjects = (list: Project[]): Project[] => {
    if (!searchQuery.trim()) return list;
    const q = searchQuery.toLowerCase();
    return list.filter(p => p.name.toLowerCase().includes(q));
  };

  const filteredStarred = filterProjects(starredProjects);
  const filteredRecent = filterProjects(recentProjects);
  const fileCount = project.file_count ?? 0;
  const metaText = `${fileCount} files`;

  if (collapsed) {
    return (
      <div className="relative w-4 flex-shrink-0">
        {onToggleCollapse && (
          <button
            onClick={onToggleCollapse}
            className="absolute top-1/2 -translate-y-1/2 left-0 z-10 w-4 h-10 flex items-center justify-center rounded-r-md bg-zinc-800/80 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 transition-colors"
            title="Expand sidebar"
          >
            <ChevronRight size={12} />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="group relative w-52 h-full flex flex-col border-r border-zinc-800/40 pt-16">
      {/* Collapse toggle */}
      {onToggleCollapse && (
        <button
          onClick={onToggleCollapse}
          className="absolute top-1/2 -translate-y-1/2 right-0 z-10 w-4 h-10 flex items-center justify-center rounded-l-md bg-zinc-800/80 text-zinc-500 hover:text-zinc-200 hover:bg-zinc-700 transition-colors opacity-0 group-hover:opacity-100"
          title="Collapse sidebar"
        >
          <ChevronLeft size={12} />
        </button>
      )}

      {/* Header */}
      <div className="relative px-3 pt-4 pb-3" ref={dropdownRef}>
        <div className="flex items-center gap-2">
          <button
            onClick={onBackToList}
            className="p-1 rounded hover:bg-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors duration-120"
            title="Back to projects"
          >
            <ArrowLeft size={16} />
          </button>
          <button
            onClick={handleToggleDropdown}
            className="flex items-center gap-2 flex-1 min-w-0 rounded-md px-1.5 py-1 hover:bg-zinc-800 transition-colors duration-120"
          >
            <ProjectAvatar name={project.name} />
            <div className="flex-1 min-w-0 text-left">
              <div className="text-sm font-medium text-zinc-100 truncate">{project.name}</div>
              <div className="text-[11px] text-zinc-500 truncate">{metaText}</div>
            </div>
            <ChevronDown size={14} className={`text-zinc-500 shrink-0 transition-transform duration-150 ${dropdownOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* Dropdown */}
        {dropdownOpen && (
          <div className="absolute left-2 right-2 top-full mt-1 z-50 bg-zinc-900 border border-zinc-700 rounded-lg shadow-2xl animate-in slide-in-from-top-1 duration-150 overflow-hidden">
            {/* Search */}
            <div className="p-2 border-b border-zinc-800">
              <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-zinc-800">
                <Search size={14} className="text-zinc-500 shrink-0" />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder="Switch project..."
                  className="flex-1 bg-transparent text-sm text-zinc-200 placeholder-zinc-500 outline-none"
                />
              </div>
            </div>

            <div className="max-h-64 overflow-y-auto py-1">
              {/* Starred */}
              {filteredStarred.length > 0 && (
                <DropdownSection icon={<Star size={12} />} label="Starred">
                  {filteredStarred.map(p => (
                    <DropdownItem key={p.id} project={p} onClick={() => handleProjectClick(p)} />
                  ))}
                </DropdownSection>
              )}

              {/* Recent */}
              {filteredRecent.length > 0 && (
                <DropdownSection icon={<Clock size={12} />} label="Recent">
                  {filteredRecent.map(p => (
                    <DropdownItem key={p.id} project={p} onClick={() => handleProjectClick(p)} />
                  ))}
                </DropdownSection>
              )}

              {filteredStarred.length === 0 && filteredRecent.length === 0 && (
                <div className="px-3 py-4 text-center text-xs text-zinc-500">No projects found</div>
              )}
            </div>

            {/* Footer */}
            <div className="border-t border-zinc-800">
              <button
                onClick={() => { handleCloseDropdown(); onBackToList(); }}
                className="flex items-center gap-2 w-full px-3 py-2 text-sm text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800 transition-colors duration-120"
              >
                <LayoutGrid size={14} />
                All Projects
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Nav Menu */}
      <nav className="px-2 py-2 space-y-0.5">
        {NAV_SECTIONS.map(section => {
          if (section.key.startsWith('divider')) {
            return <div key={section.key} className="my-2.5 mx-3 border-t border-zinc-800/40" />;
          }
          const Icon = section.icon!;
          const isActive = activeSection === section.key;
          const count = sectionCounts[section.key];
          return (
            <button
              key={section.key}
              onClick={() => onSectionChange(section.key)}
              className={`flex items-center gap-3 w-full px-3 py-2 rounded-md text-[13px] transition-colors duration-120 ${
                isActive
                  ? 'text-zinc-100 bg-zinc-800/80'
                  : 'text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50'
              }`}
            >
              <Icon size={16} className={`shrink-0 ${!isActive && section.iconColor ? section.iconColor : ''}`} />
              <span className="flex-1 text-left">{section.label}</span>
              {count != null && count > 0 && (
                <span className="text-[10px] min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-zinc-800 text-zinc-400 font-medium">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Project Info */}
      <ProjectInfoFooter project={project} />
    </div>
  );
}

/* --- Dropdown Sub-components --- */

function DropdownSection({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="py-1">
      <div className="flex items-center gap-1.5 px-3 py-1 text-[11px] font-medium text-zinc-500 uppercase tracking-wider">
        {icon}
        {label}
      </div>
      {children}
    </div>
  );
}

function DropdownItem({ project, onClick }: { project: Project; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-zinc-300 hover:text-zinc-100 hover:bg-zinc-800 transition-colors duration-120"
    >
      <ProjectAvatar name={project.name} />
      <span className="truncate">{project.name}</span>
    </button>
  );
}

function ProjectInfoFooter({ project }: { project: Project }) {
  const created = new Date(project.created_at);
  const month = created.toLocaleString('en', { month: 'short' });
  const day = created.getDate();
  const year = created.getFullYear();

  return (
    <div className="px-3 py-3 border-t border-zinc-800/40">
      {project.description && (
        <p className="text-[11px] text-zinc-500 mb-3 line-clamp-3 leading-relaxed">
          {project.description}
        </p>
      )}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2 text-[11px] text-zinc-600">
          <Calendar size={12} className="shrink-0" />
          <span>Created {month} {day}, {year}</span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-zinc-600">
          <Tag size={12} className="shrink-0" />
          <span>{project.project_type === 'external' ? 'External' : 'Internal'}</span>
        </div>
        {project.is_starred && (
          <div className="flex items-center gap-2 text-[11px] text-yellow-600/60">
            <Star size={12} className="shrink-0" />
            <span>Starred</span>
          </div>
        )}
      </div>
    </div>
  );
}
