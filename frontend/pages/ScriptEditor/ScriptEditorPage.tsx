import { useState, useEffect, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Save } from 'lucide-react';
import { useScriptCanvasStore, type ScriptNode } from '../../stores/scriptCanvasStore';
import { ScriptCanvas } from '../../features/script/ScriptCanvas';
import { ScriptToolbar } from '../../features/script/ScriptToolbar';
import { CreateStoryDialog } from '../../features/script/CreateStoryDialog';
import { ExpandChapterDialog } from '../../features/script/ExpandChapterDialog';
import { CreateBranchDialog } from '../../features/script/CreateBranchDialog';
import { ScriptAssetsSidebar } from '../../features/script/ScriptAssetsSidebar';
import {
  fetchScriptProject,
  updateScriptProject,
  syncScriptCanvas,
  updateScriptViewport,
} from '../../services/scriptService';
import type { ScriptChapter } from '../../types';

function mapChaptersToNodes(chapters: ScriptChapter[]): ScriptNode[] {
  return chapters.map((ch) => ({
    id: String(ch.id),
    type: 'chapterNode' as const,
    position: { x: ch.position_x, y: ch.position_y },
    data: {
      title: ch.title ?? '',
      summary: ch.summary ?? '',
      content: ch.content ?? '',
      chapterNumber: ch.chapter_number ?? 0,
      branchLabel: ch.branch_label,
      branchType: ch.branch_type,
    },
    ...(ch.width ? { width: ch.width } : {}),
    ...(ch.height ? { height: ch.height } : {}),
  }));
}

function mapChaptersToEdges(chapters: ScriptChapter[]): { id: string; source: string; target: string }[] {
  return chapters
    .filter((ch) => ch.parent_chapter_id)
    .map((ch) => ({
      id: `edge-${ch.parent_chapter_id}-${ch.id}`,
      source: String(ch.parent_chapter_id),
      target: String(ch.id),
    }));
}

export function ScriptEditorPage() {
  const navigate = useNavigate();
  const { teamId, projectId, scriptId } = useParams<{
    teamId: string;
    projectId: string;
    scriptId: string;
  }>();

  const setCanvasData = useScriptCanvasStore((s) => s.setCanvasData);
  const nodes = useScriptCanvasStore((s) => s.nodes);
  const viewport = useScriptCanvasStore((s) => s.currentViewport);
  const expandDialog = useScriptCanvasStore((s) => s.expandDialog);
  const closeExpandDialog = useScriptCanvasStore((s) => s.closeExpandDialog);
  const branchDialog = useScriptCanvasStore((s) => s.branchDialog);
  const closeBranchDialog = useScriptCanvasStore((s) => s.closeBranchDialog);

  const [scriptName, setScriptName] = useState('Untitled Script');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showCreateStory, setShowCreateStory] = useState(false);
  const [saving, setSaving] = useState(false);

  // Load script on mount
  useEffect(() => {
    if (!scriptId) {
      setLoading(false);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const project = await fetchScriptProject(scriptId);
        if (cancelled) return;
        setScriptName(project.name);
        const chapterNodes = mapChaptersToNodes(project.chapters);
        const chapterEdges = mapChaptersToEdges(project.chapters);
        setCanvasData(chapterNodes, chapterEdges);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setLoadError(message);
        console.error('[ScriptEditorPage] Failed to load:', message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [scriptId, setCanvasData]);

  // Save handler
  const handleSave = useCallback(async () => {
    if (!scriptId || saving) return;
    setSaving(true);
    try {
      const chaptersToSync = nodes.map((n) => ({
        id: n.id,
        title: n.data.title,
        summary: n.data.summary,
        content: n.data.content,
        chapter_number: n.data.chapterNumber,
        branch_label: n.data.branchLabel,
        branch_type: n.data.branchType,
        position_x: n.position.x,
        position_y: n.position.y,
        ...(n.width ? { width: n.width } : {}),
        ...(n.height ? { height: n.height } : {}),
      }));

      await syncScriptCanvas(scriptId, {
        added_chapters: [],
        updated_chapters: chaptersToSync,
        deleted_chapter_ids: [],
      });
      await updateScriptViewport(scriptId, viewport);
    } catch (err) {
      console.error('[ScriptEditorPage] Save failed:', err);
    } finally {
      setSaving(false);
    }
  }, [scriptId, nodes, viewport, saving]);

  // Name editing
  const handleNameBlur = useCallback(async () => {
    setEditingName(false);
    const trimmed = nameInput.trim();
    if (trimmed && trimmed !== scriptName && scriptId) {
      setScriptName(trimmed);
      await updateScriptProject(scriptId, { name: trimmed }).catch(console.error);
    }
  }, [nameInput, scriptName, scriptId]);

  const handleBack = () => {
    navigate(`/team/${teamId}/projects/${projectId}?tab=scripts`);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen bg-zinc-950">
        <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex flex-col items-center justify-center h-screen bg-zinc-950 gap-3">
        <p className="text-sm text-red-400">Failed to load script: {loadError}</p>
        <button onClick={handleBack} className="text-sm text-indigo-400 hover:underline">
          Back to project
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800 bg-zinc-900">
        <button onClick={handleBack} className="text-zinc-500 hover:text-zinc-300 p-1">
          <ArrowLeft size={18} />
        </button>
        {editingName ? (
          <input
            className="text-sm font-semibold text-white bg-transparent outline-none border-b border-indigo-500"
            value={nameInput}
            onChange={(e) => setNameInput(e.target.value)}
            onBlur={handleNameBlur}
            onKeyDown={(e) => e.key === 'Enter' && handleNameBlur()}
            autoFocus
          />
        ) : (
          <button
            className="text-sm font-semibold text-zinc-100 hover:text-white"
            onDoubleClick={() => {
              setNameInput(scriptName);
              setEditingName(true);
            }}
          >
            {scriptName}
          </button>
        )}
        <div className="flex-1" />
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-50"
        >
          <Save size={14} />
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      {/* Toolbar */}
      <ScriptToolbar onCreateStory={() => setShowCreateStory(true)} />

      {/* Sidebar + Canvas */}
      <div className="flex-1 flex overflow-hidden">
        <ScriptAssetsSidebar />
        <div className="flex-1 relative">
          <ScriptCanvas />
        </div>
      </div>

      {/* Create Story Dialog */}
      <CreateStoryDialog
        isOpen={showCreateStory}
        onClose={() => setShowCreateStory(false)}
      />

      {/* AI Expand Dialog */}
      {expandDialog && (
        <ExpandChapterDialog
          isOpen={expandDialog.isOpen}
          onClose={closeExpandDialog}
          chapterId={expandDialog.chapterId}
          title={expandDialog.title}
          summary={expandDialog.summary}
        />
      )}

      {/* AI Branch Dialog */}
      {branchDialog && (
        <CreateBranchDialog
          isOpen={branchDialog.isOpen}
          onClose={closeBranchDialog}
          chapterId={branchDialog.chapterId}
          title={branchDialog.title}
          summary={branchDialog.summary}
        />
      )}
    </div>
  );
}
