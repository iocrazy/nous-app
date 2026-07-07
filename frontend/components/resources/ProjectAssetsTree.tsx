// frontend/components/resources/ProjectAssetsTree.tsx
// Left-hand Project → Canvas tree for the Project Assets view.
// A synthetic "Chat Uploads" root sits above the projects.

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, Layers, MessageSquare, Plus, Sparkles } from 'lucide-react';
import {
  fetchProjectAssetsTree,
  type ProjectAssetTreeNode,
} from '../../services/projectAssetsService';
import { createCanvas } from '../../features/canvas-core/services/canvasService';
import { useToast } from '../Toast';

export type ProjectAssetsSelection =
  | { kind: 'chat-uploads' }
  | { kind: 'generations' }
  | { kind: 'canvas'; canvasId: string; canvasName: string };

interface Props {
  selection: ProjectAssetsSelection;
  onSelect: (sel: ProjectAssetsSelection) => void;
  chatUploadsCount: number;
}

export const ProjectAssetsTree: React.FC<Props> = ({ selection, onSelect, chatUploadsCount }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();
  const { addToast } = useToast();
  const [tree, setTree] = useState<ProjectAssetTreeNode[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [creatingProjectId, setCreatingProjectId] = useState<string | null>(null);

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

  // Create a fresh (empty) canvas and drop the user straight into the editor.
  // The new canvas has no nodes yet, so batch A's orphan filter keeps it out
  // of THIS tree until a node is added — that's why we navigate rather than
  // refresh the tree in place.
  const handleCreateCanvas = async (projectId: string) => {
    if (creatingProjectId) return;
    setCreatingProjectId(projectId);
    try {
      const canvas = await createCanvas(projectId, {
        name: t('projectAssets.untitledCanvas', 'Untitled Canvas'),
      });
      navigate(`/team/${teamId}/canvas/${canvas.id}`);
    } catch (err) {
      console.error('[ProjectAssetsTree] create canvas failed:', err);
      addToast(t('projectAssets.createFailed', 'Failed to create canvas'), 'error');
    } finally {
      setCreatingProjectId(null);
    }
  };

  return (
    <div className="w-60 shrink-0 border-r border-line overflow-y-auto py-2 text-sm">
      <button
        onClick={() => onSelect({ kind: 'chat-uploads' })}
        className={`flex items-center gap-2 w-full px-3 py-1.5 rounded-md ${
          selection.kind === 'chat-uploads'
            ? 'bg-island-2 text-content-2'
            : 'text-content-2 hover:bg-island-2'
        }`}
      >
        <MessageSquare size={15} className="opacity-70 shrink-0" />
        <span className="flex-1 truncate text-left">{t('projectAssets.chatUploads')}</span>
        <span className="text-xs text-content-3">{chatUploadsCount}</span>
      </button>

      <button
        onClick={() => onSelect({ kind: 'generations' })}
        className={`flex items-center gap-2 w-full px-3 py-1.5 rounded-md ${
          selection.kind === 'generations'
            ? 'bg-island-2 text-content-2'
            : 'text-content-2 hover:bg-island-2'
        }`}
      >
        <Sparkles size={15} className="opacity-70 shrink-0" />
        <span className="flex-1 truncate text-left">{t('projectAssets.generations', 'Generations')}</span>
      </button>

      <div className="mx-2 my-2 border-t border-line" />

      {loading && <div className="px-3 py-2 text-content-3">{t('common.loading')}</div>}

      {!loading && tree.map((project) => (
        <div key={project.project_id}>
          <div className="group flex items-center w-full px-2 py-1.5 text-content-2">
            <button
              onClick={() => toggle(project.project_id)}
              className="flex items-center gap-1.5 flex-1 min-w-0 hover:text-content-2"
            >
              {expanded.has(project.project_id)
                ? <ChevronDown size={14} className="shrink-0" />
                : <ChevronRight size={14} className="shrink-0" />}
              <span className="flex-1 truncate text-left font-medium">{project.name}</span>
            </button>
            <button
              onClick={() => handleCreateCanvas(project.project_id)}
              disabled={creatingProjectId === project.project_id}
              title={t('projectAssets.newCanvas', 'New Canvas')}
              aria-label={t('projectAssets.newCanvas', 'New Canvas')}
              className="shrink-0 p-1 rounded text-content-3 hover:text-content-2 hover:bg-island-2 opacity-0 group-hover:opacity-100 focus:opacity-100 disabled:opacity-40"
            >
              <Plus size={14} />
            </button>
          </div>

          {expanded.has(project.project_id) && project.canvases.map((canvas) => (
            <button
              key={canvas.canvas_id}
              onClick={() => onSelect({ kind: 'canvas', canvasId: canvas.canvas_id, canvasName: canvas.canvas_name })}
              className={`flex items-center gap-2 w-full pl-7 pr-3 py-1.5 rounded-md ${
                selection.kind === 'canvas' && selection.canvasId === canvas.canvas_id
                  ? 'bg-island-2 text-content-2'
                  : 'text-content-2 hover:bg-island-2'
              }`}
            >
              {canvas.kind === 'smart'
                ? <Sparkles size={14} className="opacity-70 shrink-0" />
                : <Layers size={14} className="opacity-70 shrink-0" />}
              <span className="flex-1 truncate text-left">{canvas.canvas_name}</span>
              <span className="text-xs text-content-3">{canvas.asset_count}</span>
            </button>
          ))}

          {expanded.has(project.project_id) && project.canvases.length === 0 && (
            <button
              onClick={() => handleCreateCanvas(project.project_id)}
              disabled={creatingProjectId === project.project_id}
              aria-label={t('projectAssets.newCanvas', 'New Canvas')}
              className="flex items-center gap-1.5 w-full pl-7 pr-3 py-1 rounded-md text-xs text-content-3 hover:text-content-2 hover:bg-island-2 disabled:opacity-40"
            >
              <Plus size={13} className="shrink-0" />
              <span className="truncate text-left">{t('projectAssets.newCanvas', 'New Canvas')}</span>
            </button>
          )}
        </div>
      ))}
    </div>
  );
};
