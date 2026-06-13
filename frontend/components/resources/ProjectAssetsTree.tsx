// frontend/components/resources/ProjectAssetsTree.tsx
// Left-hand Project → Canvas tree for the Project Assets view.
// A synthetic "Chat Uploads" root sits above the projects.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Layers, MessageSquare, Sparkles } from 'lucide-react';
import {
  fetchProjectAssetsTree,
  type ProjectAssetTreeNode,
} from '../../services/projectAssetsService';

export type ProjectAssetsSelection =
  | { kind: 'chat-uploads' }
  | { kind: 'canvas'; canvasId: string; canvasName: string };

interface Props {
  selection: ProjectAssetsSelection;
  onSelect: (sel: ProjectAssetsSelection) => void;
  chatUploadsCount: number;
}

export const ProjectAssetsTree: React.FC<Props> = ({ selection, onSelect, chatUploadsCount }) => {
  const { t } = useTranslation();
  const [tree, setTree] = useState<ProjectAssetTreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchProjectAssetsTree()
      .then((data) => {
        if (cancelled) return;
        setTree(data);
        setExpanded(new Set(data.filter((p) => p.canvases.length).map((p) => p.project_id)));
      })
      .catch((err) => console.error('[ProjectAssetsTree] load failed:', err))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const toggle = (projectId: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });

  return (
    <div className="w-60 shrink-0 border-r border-ink-800/60 overflow-y-auto py-2 text-sm">
      <button
        onClick={() => onSelect({ kind: 'chat-uploads' })}
        className={`flex items-center gap-2 w-full px-3 py-1.5 rounded-md ${
          selection.kind === 'chat-uploads' ? 'bg-ink-800 text-ink-100' : 'text-ink-300 hover:bg-ink-800/60'
        }`}
      >
        <MessageSquare size={15} className="opacity-70 shrink-0" />
        <span className="flex-1 truncate text-left">{t('projectAssets.chatUploads')}</span>
        <span className="text-xs text-ink-500">{chatUploadsCount}</span>
      </button>

      <div className="mx-2 my-2 border-t border-ink-800/50" />

      {loading && <div className="px-3 py-2 text-ink-500">{t('common.loading')}</div>}

      {!loading && tree.map((project) => (
        <div key={project.project_id}>
          <button
            onClick={() => toggle(project.project_id)}
            className="flex items-center gap-1.5 w-full px-2 py-1.5 text-ink-400 hover:text-ink-200"
          >
            {expanded.has(project.project_id)
              ? <ChevronDown size={14} className="shrink-0" />
              : <ChevronRight size={14} className="shrink-0" />}
            <span className="flex-1 truncate text-left font-medium">{project.name}</span>
          </button>

          {expanded.has(project.project_id) && project.canvases.map((canvas) => (
            <button
              key={canvas.canvas_id}
              onClick={() => onSelect({ kind: 'canvas', canvasId: canvas.canvas_id, canvasName: canvas.canvas_name })}
              className={`flex items-center gap-2 w-full pl-7 pr-3 py-1.5 rounded-md ${
                selection.kind === 'canvas' && selection.canvasId === canvas.canvas_id
                  ? 'bg-ink-800 text-ink-100' : 'text-ink-300 hover:bg-ink-800/60'
              }`}
            >
              {canvas.kind === 'smart'
                ? <Sparkles size={14} className="opacity-70 shrink-0" />
                : <Layers size={14} className="opacity-70 shrink-0" />}
              <span className="flex-1 truncate text-left">{canvas.canvas_name}</span>
              <span className="text-xs text-ink-500">{canvas.asset_count}</span>
            </button>
          ))}

          {expanded.has(project.project_id) && project.canvases.length === 0 && (
            <div className="pl-7 pr-3 py-1 text-xs text-ink-600">{t('projectAssets.noCanvases')}</div>
          )}
        </div>
      ))}
    </div>
  );
};
