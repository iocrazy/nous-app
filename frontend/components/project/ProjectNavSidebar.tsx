import { useState, useEffect, useRef, useCallback } from 'react';
import {
  ChevronDown,
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
}

const AVATAR_COLORS = [
  'bg-rose-500', 'bg-amber-500', 'bg-emerald-500', 'bg-cyan-500',
  'bg-blue-500', 'bg-indigo-500', 'bg-violet-500', 'bg-pink-500',
] as const;

const NAV_SECTIONS = [
  { key: 'files', label: 'Files', icon: FolderOpen },
  { key: 'scripts', label: 'Scripts', icon: FileText },
  { key: 'storyboard', label: 'Storyboard', icon: Clapperboard },
  { key: 'output', label: 'Output', icon: Download },
  { key: 'tasks', label: 'Tasks', icon: KanbanSquare },
  { key: 'divider-1', label: '', icon: null },
  { key: 'shares', label: 'Shares', icon: Share2 },
  { key: 'trash', label: 'Trash', icon: Trash2 },
  { key: 'divider-2', label: '', icon: null },
  { key: 'settings', label: 'Settings', icon: Settings },
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

  return (
    <div className="w-56 h-full flex flex-col border-r border-zinc-800/40 pt-16">
      {/* Header */}
      <div className="relative p-3" ref={dropdownRef}>
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
      <nav className="px-1 py-1">
        {NAV_SECTIONS.map(section => {
          if (section.key.startsWith('divider')) {
            return <div key={section.key} className="my-1.5 mx-2 border-t border-zinc-800/50" />;
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
              <Icon size={16} className="shrink-0" />
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
