// frontend/components/AILibrary/SkillList.tsx
// Left rail of the paperclip-style Skills split-pane: filterable list of
// skills, each row expandable into a file tree (SKILL.md + references/
// + scripts/ + assets/).
//
// - Click a skill header → navigates to /ai-library/skills/:slug (SKILL.md)
// - Expand chevron → shows nested file tree
// - Click a file → navigates to /ai-library/skills/:slug/files/:path
// - Preset badges use the same convention as SkillEditor (is_public && !project_id)
// - "+ New" button opens NewSkillModal (parent owns that state)
//
// Auto-expand: when the URL matches a file path inside a skill, expand
// that skill so the user can see where they are after a cold load or
// deep-link.

import React, { useMemo, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronDown,
  ChevronRight,
  FileText,
  FolderOpen,
  Folder,
  Package,
  Plus,
  Search,
  User as UserIcon,
  Users,
  Wrench,
  FileArchive,
} from 'lucide-react';
import type { AILibrarySkill, AILibrarySkillFile } from '../../types';

interface SkillListProps {
  skills: AILibrarySkill[];
  activeSkillSlug: string | null;
  activeFilePath: string | null;
  onSelectSkill: (slug: string) => void;
  onSelectFile: (slug: string, filePath: string) => void;
  onNewSkill: () => void;
}

/** Preset = system-seed: is_public && no team/project owner. */
const isPreset = (s: AILibrarySkill): boolean =>
  s.is_public && s.team_id == null && s.project_id == null;

/** Source icon driven by scope — matches SkillEditor's ``skillSource``. */
function sourceIcon(s: AILibrarySkill): React.ElementType {
  if (s.is_public && s.team_id == null && s.project_id == null) return Package;
  if (s.team_id != null || s.project_id != null) return Users;
  return UserIcon;
}

interface TreeNode {
  name: string;
  /** Full path relative to the skill root (empty for the root dir). */
  path: string;
  children: TreeNode[];
  /** Present when this node is a leaf (an actual file row). */
  file?: AILibrarySkillFile;
}

/** Build a nested folder tree from a flat file list (skill.files). */
function buildTree(files: AILibrarySkillFile[]): TreeNode {
  const root: TreeNode = { name: '', path: '', children: [] };
  for (const f of files) {
    const segments = f.path.split('/').filter(Boolean);
    let cursor = root;
    let acc = '';
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      acc = acc ? `${acc}/${seg}` : seg;
      const isLeaf = i === segments.length - 1;
      let next = cursor.children.find((c) => c.name === seg);
      if (!next) {
        next = { name: seg, path: acc, children: [] };
        cursor.children.push(next);
      }
      if (isLeaf) next.file = f;
      cursor = next;
    }
  }
  return root;
}

/** Pick an icon for a leaf file based on its path / file_type. */
function fileIcon(file: AILibrarySkillFile | undefined) {
  const ft = file?.file_type;
  if (ft === 'script') return Wrench;
  if (ft === 'binary-ref') return FileArchive;
  return FileText;
}

interface TreeProps {
  node: TreeNode;
  depth: number;
  activeFilePath: string | null;
  expandedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  onSelectFile: (filePath: string) => void;
}

const TreeRow: React.FC<TreeProps> = ({
  node,
  depth,
  activeFilePath,
  expandedDirs,
  onToggleDir,
  onSelectFile,
}) => {
  // Root node isn't rendered — only its children.
  if (depth === 0) {
    return (
      <div>
        {node.children.map((c) => (
          <TreeRow
            key={c.path}
            node={c}
            depth={depth + 1}
            activeFilePath={activeFilePath}
            expandedDirs={expandedDirs}
            onToggleDir={onToggleDir}
            onSelectFile={onSelectFile}
          />
        ))}
      </div>
    );
  }

  const paddingLeft = 12 + depth * 10;
  const isFile = node.file !== undefined;

  if (isFile) {
    const Icon = fileIcon(node.file);
    const active = activeFilePath === node.path;
    return (
      <button
        type="button"
        onClick={() => onSelectFile(node.path)}
        className={`flex w-full items-center gap-2 py-1 text-left text-[12px] transition-colors ${
          active
            ? 'bg-indigo-500/12 text-indigo-200'
            : 'text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200'
        }`}
        style={{ paddingLeft }}
      >
        <Icon
          size={12}
          className={`shrink-0 ${active ? 'text-indigo-300' : 'text-zinc-500'}`}
        />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  const expanded = expandedDirs.has(node.path);
  const FolderIcon = expanded ? FolderOpen : Folder;
  return (
    <div>
      <button
        type="button"
        onClick={() => onToggleDir(node.path)}
        className="flex w-full items-center gap-1.5 py-1 text-left text-[12px] text-zinc-400 transition-colors hover:bg-zinc-800/40 hover:text-zinc-200"
        style={{ paddingLeft }}
      >
        {expanded ? (
          <ChevronDown size={10} className="shrink-0 text-zinc-600" />
        ) : (
          <ChevronRight size={10} className="shrink-0 text-zinc-600" />
        )}
        <FolderIcon size={12} className="shrink-0 text-zinc-600" />
        <span className="truncate">{node.name}/</span>
      </button>
      {expanded && (
        <div>
          {node.children.map((c) => (
            <TreeRow
              key={c.path}
              node={c}
              depth={depth + 1}
              activeFilePath={activeFilePath}
              expandedDirs={expandedDirs}
              onToggleDir={onToggleDir}
              onSelectFile={onSelectFile}
            />
          ))}
        </div>
      )}
    </div>
  );
};

/** Collect every ancestor directory of a path (so auto-expand can open them). */
function ancestorDirs(path: string): string[] {
  const segments = path.split('/').filter(Boolean);
  const out: string[] = [];
  let acc = '';
  for (let i = 0; i < segments.length - 1; i++) {
    acc = acc ? `${acc}/${segments[i]}` : segments[i];
    out.push(acc);
  }
  return out;
}

export const SkillList: React.FC<SkillListProps> = ({
  skills,
  activeSkillSlug,
  activeFilePath,
  onSelectSkill,
  onSelectFile,
  onNewSkill,
}) => {
  const { t } = useTranslation();
  const [filter, setFilter] = useState('');
  const [expandedSkillSlug, setExpandedSkillSlug] = useState<string | null>(
    activeSkillSlug,
  );
  // Per-skill directory expansion. Keyed by `${slug}::${dirPath}`.
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  // Auto-expand active skill + any ancestor dirs of the active file so a
  // cold deep-link lands with the tree already opened to the right place.
  useEffect(() => {
    if (activeSkillSlug) setExpandedSkillSlug(activeSkillSlug);
    if (activeSkillSlug && activeFilePath) {
      setExpandedDirs((prev) => {
        const next = new Set(prev);
        for (const d of ancestorDirs(activeFilePath)) {
          next.add(`${activeSkillSlug}::${d}`);
        }
        return next;
      });
    }
  }, [activeSkillSlug, activeFilePath]);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter((s) => {
      const haystack = [
        s.name,
        s.slug,
        s.description ?? '',
        s.category ?? '',
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [skills, filter]);

  const toggleSkill = (slug: string) => {
    setExpandedSkillSlug((prev) => (prev === slug ? null : slug));
  };

  const toggleDir = (slug: string, dirPath: string) => {
    setExpandedDirs((prev) => {
      const key = `${slug}::${dirPath}`;
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="flex h-full w-[19rem] flex-col border-r border-zinc-800/60 bg-zinc-950/40">
      {/* Header — Paperclip: "Skills" title + count; + button to the right */}
      <div className="flex items-start justify-between border-b border-zinc-800/60 px-4 py-3">
        <div className="flex flex-col">
          <span className="text-[15px] font-semibold text-zinc-100">
            {t('aiLibrary.skills.listTitle', 'Skills')}
          </span>
          <span className="text-[11px] text-zinc-500">
            {t('aiLibrary.skills.countAvailable', '{{count}} available', {
              count: skills.length,
            })}
          </span>
        </div>
        <button
          type="button"
          onClick={onNewSkill}
          className="flex h-7 w-7 items-center justify-center rounded text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-100"
          title={t('aiLibrary.skills.newSkillButton', 'New Skill')}
          aria-label={t('aiLibrary.skills.newSkillButton', 'New Skill')}
        >
          <Plus size={14} />
        </button>
      </div>

      {/* Filter */}
      <div className="border-b border-zinc-800/60 px-3 py-2">
        <div className="relative">
          <Search
            size={12}
            className="absolute left-2 top-1/2 -translate-y-1/2 text-zinc-600"
          />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('aiLibrary.skills.filterPlaceholder', 'Filter skills...')}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 py-1.5 pl-7 pr-2 text-[12px] text-zinc-200 placeholder-zinc-600 focus:border-indigo-500 focus:outline-none"
          />
        </div>
      </div>

      {/* List */}
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {filtered.length === 0 ? (
          <div className="px-4 py-6 text-center text-[12px] text-zinc-600">
            {t('aiLibrary.skills.noMatches', 'No skills match your filter.')}
          </div>
        ) : (
          filtered.map((s) => {
            const preset = isPreset(s);
            const skillActive = activeSkillSlug === s.slug;
            const expanded = expandedSkillSlug === s.slug;
            const tree = buildTree(s.files ?? []);
            // Scope expandedDirs set to this skill for the Tree renderer.
            const localExpanded = new Set<string>();
            for (const key of expandedDirs) {
              if (key.startsWith(`${s.slug}::`)) {
                localExpanded.add(key.slice(s.slug.length + 2));
              }
            }

            const SrcIcon = sourceIcon(s);
            return (
              <div
                key={s.slug ?? String(s.id)}
                className="border-b border-zinc-900/50 last:border-b-0"
              >
                <div className="group flex items-center gap-1 px-3 py-1.5 hover:bg-zinc-800/30">
                  <button
                    type="button"
                    onClick={() => onSelectSkill(s.slug ?? String(s.id))}
                    className={`flex min-w-0 flex-1 items-center gap-2 text-left transition-colors ${
                      skillActive
                        ? 'text-zinc-100'
                        : 'text-zinc-300 hover:text-zinc-100'
                    }`}
                    title={preset ? t('aiLibrary.skills.presetTooltip', 'Bundled MediaHub preset') : s.name}
                  >
                    <SrcIcon
                      size={12}
                      className={`shrink-0 ${
                        skillActive ? 'text-zinc-300' : 'text-zinc-500'
                      }`}
                    />
                    <span className="min-w-0 truncate text-[13px] font-medium">
                      {s.name}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleSkill(s.slug ?? String(s.id))}
                    className="flex h-6 w-6 shrink-0 items-center justify-center rounded text-zinc-500 opacity-70 transition-opacity hover:bg-zinc-800/60 hover:text-zinc-200 group-hover:opacity-100"
                    aria-label={expanded ? 'Collapse' : 'Expand'}
                  >
                    {expanded ? (
                      <ChevronDown size={12} />
                    ) : (
                      <ChevronRight size={12} />
                    )}
                  </button>
                </div>

                {expanded && (
                  <div className="pb-2 pl-2 pr-1">
                    {/* SKILL.md root file, rendered as a synthetic leaf. */}
                    <button
                      type="button"
                      onClick={() => onSelectSkill(s.slug ?? String(s.id))}
                      className={`flex w-full items-center gap-2 py-1 pl-6 pr-2 text-left text-[12px] transition-colors ${
                        skillActive && !activeFilePath
                          ? 'bg-indigo-500/12 text-indigo-200'
                          : 'text-zinc-400 hover:bg-zinc-800/40 hover:text-zinc-200'
                      }`}
                    >
                      <FileText
                        size={12}
                        className={`shrink-0 ${
                          skillActive && !activeFilePath
                            ? 'text-indigo-300'
                            : 'text-zinc-500'
                        }`}
                      />
                      <span className="truncate">SKILL.md</span>
                    </button>

                    <TreeRow
                      node={tree}
                      depth={0}
                      activeFilePath={skillActive ? activeFilePath : null}
                      expandedDirs={localExpanded}
                      onToggleDir={(p) => toggleDir(s.slug ?? String(s.id), p)}
                      onSelectFile={(p) =>
                        onSelectFile(s.slug ?? String(s.id), p)
                      }
                    />
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};

export default SkillList;
