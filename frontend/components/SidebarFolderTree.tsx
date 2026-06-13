import React, { useState, useCallback, useEffect } from 'react';
import { ChevronRight, ChevronDown, Folder as FolderIcon } from 'lucide-react';
import { Folder } from '../types';
import { buildFolderTree } from '../services/resourceService';

interface SidebarFolderTreeProps {
  folders: Folder[];
  currentFolderId: string | null;
  onNavigate: (folderId: string | null) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent, folderId: string | null) => void;
}

interface FolderNodeProps {
  folder: Folder;
  depth: number;
  currentFolderId: string | null;
  expandedIds: Set<string>;
  onToggleExpand: (id: string) => void;
  onNavigate: (folderId: string) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent, folderId: string) => void;
}

const FolderNode: React.FC<FolderNodeProps> = ({
  folder,
  depth,
  currentFolderId,
  expandedIds,
  onToggleExpand,
  onNavigate,
  onDragOver,
  onDrop,
}) => {
  const isExpanded = expandedIds.has(folder.id);
  const isActive = currentFolderId === folder.id;
  const hasChildren = folder.children && folder.children.length > 0;
  const [dragOver, setDragOver] = useState(false);

  return (
    <div>
      <div
        className={`flex items-center gap-1 py-1 pr-2 rounded-md text-[13px] cursor-pointer transition-colors group ${
          isActive
            ? 'bg-ink-800 text-ink-50 font-medium'
            : dragOver
              ? 'bg-ink-800/70 text-ink-200'
              : 'text-ink-400 hover:bg-ink-800/50 hover:text-ink-200'
        }`}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('application/mediahub-items')) {
            e.preventDefault();
            e.stopPropagation();
            setDragOver(true);
          }
          onDragOver?.(e);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          setDragOver(false);
          onDrop?.(e, folder.id);
        }}
      >
        {/* Expand/collapse arrow */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) onToggleExpand(folder.id);
          }}
          className={`p-0.5 shrink-0 ${hasChildren ? 'opacity-60 hover:opacity-100' : 'opacity-0'}`}
        >
          {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </button>
        <FolderIcon size={14} className="shrink-0 opacity-70" />
        <span
          className="truncate flex-1"
          onClick={() => onNavigate(folder.id)}
        >
          {folder.name}
        </span>
      </div>

      {/* Children */}
      {isExpanded && hasChildren && (
        <div>
          {folder.children!.map((child) => (
            <FolderNode
              key={child.id}
              folder={child}
              depth={depth + 1}
              currentFolderId={currentFolderId}
              expandedIds={expandedIds}
              onToggleExpand={onToggleExpand}
              onNavigate={onNavigate}
              onDragOver={onDragOver}
              onDrop={onDrop}
            />
          ))}
        </div>
      )}
    </div>
  );
};

export const SidebarFolderTree: React.FC<SidebarFolderTreeProps> = ({
  folders,
  currentFolderId,
  onNavigate,
  onDragOver,
  onDrop,
}) => {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Auto-expand ancestors of currentFolderId
  useEffect(() => {
    if (!currentFolderId) return;
    const folderMap = new Map(folders.map((f) => [f.id, f]));
    const ancestors = new Set<string>();
    let cur = folderMap.get(currentFolderId);
    while (cur?.parent_id) {
      ancestors.add(cur.parent_id);
      cur = folderMap.get(cur.parent_id);
    }
    if (ancestors.size > 0) {
      setExpandedIds((prev) => {
        const next = new Set(prev);
        ancestors.forEach((id) => next.add(id));
        return next;
      });
    }
  }, [currentFolderId, folders]);

  const onToggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const tree = React.useMemo(() => buildFolderTree(folders), [folders]);

  if (tree.length === 0) return null;

  return (
    <div className="space-y-0.5 ml-2">
      {tree.map((folder) => (
        <FolderNode
          key={folder.id}
          folder={folder}
          depth={0}
          currentFolderId={currentFolderId}
          expandedIds={expandedIds}
          onToggleExpand={onToggleExpand}
          onNavigate={(id) => onNavigate(id)}
          onDragOver={onDragOver}
          onDrop={onDrop}
        />
      ))}
    </div>
  );
};
