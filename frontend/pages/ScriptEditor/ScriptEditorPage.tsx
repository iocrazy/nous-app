import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Save } from 'lucide-react';
import { useScriptCanvasStore, type ScriptNode } from '../../stores/scriptCanvasStore';
import { EditorTopBar } from '../../components/EditorTopBar';
import { EditorLoadingScreen } from '../../components/EditorLoadingScreen';
import { ScriptCanvas } from '../../features/script/ScriptCanvas';
import { ScriptToolbar } from '../../features/script/ScriptToolbar';
import { CreateStoryDialog } from '../../features/script/CreateStoryDialog';
import { ExpandChapterDialog } from '../../features/script/ExpandChapterDialog';
import { CreateBranchDialog } from '../../features/script/CreateBranchDialog';
import { ScriptAssetsSidebar } from '../../features/script/ScriptAssetsSidebar';
import WelcomeScreen from '../../features/script/components/WelcomeScreen';
import { ImportScriptDialog } from '../../features/script/components/ImportScriptDialog';
import { ExportDialog } from '../../features/script/components/ExportDialog';
import { ViewControls } from '../../features/script/components/ViewControls';
import { GridView } from '../../features/script/views/GridView';
import { ListView } from '../../features/script/views/ListView';
import { AIChatDrawer } from '../../components/AIChatDrawer';
import {
  fetchScriptProject,
  updateScriptProject,
  syncScriptCanvas,
  updateScriptViewport,
  type ImportedChapter,
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

function mapChaptersToEdges(
  chapters: ScriptChapter[]
): { id: string; source: string; target: string }[] {
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
  const viewMode = useScriptCanvasStore((s) => s.viewMode);

  const [scriptName, setScriptName] = useState('Untitled Script');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showCreateStory, setShowCreateStory] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [saving, setSaving] = useState(false);

  // Derived: has branch nodes
  const hasBranches = nodes.some((n) => n.data.branchType != null);

  // Auto-save timer ref
  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

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

    return () => {
      cancelled = true;
    };
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

  // Debounced auto-save (500ms after node changes settle)
  useEffect(() => {
    if (nodes.length === 0 || loading) return;

    if (autoSaveTimer.current) {
      clearTimeout(autoSaveTimer.current);
    }

    autoSaveTimer.current = setTimeout(() => {
      void handleSave();
    }, 500);

    return () => {
      if (autoSaveTimer.current) {
        clearTimeout(autoSaveTimer.current);
      }
    };
    // handleSave is intentionally excluded to avoid re-triggering on its own change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, loading]);

  // Name editing
  const handleNameBlur = useCallback(async () => {
    setEditingName(false);
    const trimmed = nameInput.trim();
    if (trimmed && trimmed !== scriptName && scriptId) {
      setScriptName(trimmed);
      await updateScriptProject(scriptId, { name: trimmed }).catch(console.error);
    }
  }, [nameInput, scriptName, scriptId]);

  // Called after successful import — reload data
  const handleImportComplete = useCallback(
    (_chapters: ImportedChapter[]) => {
      if (!scriptId) return;
      void (async () => {
        try {
          const project = await fetchScriptProject(scriptId);
          const chapterNodes = mapChaptersToNodes(project.chapters);
          const chapterEdges = mapChaptersToEdges(project.chapters);
          setCanvasData(chapterNodes, chapterEdges);
        } catch (err) {
          console.error('[ScriptEditorPage] Reload after import failed:', err);
        }
      })();
    },
    [scriptId, setCanvasData]
  );

  // Navigate to a chapter node in canvas view
  const handleNavigateToChapter = useCallback((_nodeId: string) => {
    useScriptCanvasStore.getState().setViewMode('canvas');
    // ReactFlow fitView to node is handled by ScriptCanvas when viewMode changes
  }, []);

  const handleBack = () => {
    navigate(`/team/${teamId}/projects/${projectId}?tab=scripts`);
  };

  const hasChapters = nodes.length > 0;

  return (
    <div className="h-screen w-screen overflow-hidden flex flex-col bg-zinc-950">
      {/* Fullscreen loading overlay */}
      <EditorLoadingScreen visible={loading} />

      {/* Error state (shown after loading completes with an error) */}
      {!loading && loadError && (
        <div className="flex flex-col items-center justify-center flex-1 gap-3">
          <p className="text-sm text-red-400">Failed to load script: {loadError}</p>
          <button onClick={handleBack} className="text-sm text-indigo-400 hover:underline">
            Back to project
          </button>
        </div>
      )}

      {/* Editor UI — hidden while loading or on error */}
      {!loadError && (
        <>
          <EditorTopBar
            projectName={scriptName}
            onBack={handleBack}
            onExport={() => setShowExport(true)}
            onImport={() => setShowImport(true)}
            onSave={handleSave}
            onRename={async (newName) => {
              if (newName !== scriptName && scriptId) {
                setScriptName(newName);
                await updateScriptProject(scriptId, { name: newName }).catch(console.error);
              }
            }}
            saving={saving}
          />
          {/* AI Chat is opened via the bottom-right floating button
              rendered by AIChatDrawer (⌘I to toggle). */}


          {/* Sidebar + main content */}
          <div className="flex flex-1 overflow-hidden">
            <ScriptAssetsSidebar
              collapsed={sidebarCollapsed}
              onToggle={() => setSidebarCollapsed((c) => !c)}
            />

            {/* Main content area */}
            <div className="flex-1 relative overflow-hidden">
              {!hasChapters ? (
                <WelcomeScreen
                  onImport={() => setShowImport(true)}
                  onCreateStory={() => setShowCreateStory(true)}
                  onBack={handleBack}
                />
              ) : viewMode === 'grid' ? (
                <>
                  <GridView onNavigateToChapter={handleNavigateToChapter} />
                  <ViewControls />
                </>
              ) : viewMode === 'list' ? (
                <>
                  <ListView onNavigateToChapter={handleNavigateToChapter} />
                  <ViewControls />
                </>
              ) : (
                /* Canvas mode: ScriptCanvas renders ViewControls internally (with zoom) */
                <ScriptCanvas />
              )}
            </div>

            {/* AIChatDrawer mounted below — its FAB lives bottom-right
                of the viewport, so it doesn't belong inline here. */}
          </div>
        </>
      )}


      {/* Create Story Dialog */}
      <CreateStoryDialog
        isOpen={showCreateStory}
        onClose={() => setShowCreateStory(false)}
      />

      {/* Import Dialog */}
      {scriptId && (
        <ImportScriptDialog
          open={showImport}
          onClose={() => setShowImport(false)}
          scriptId={scriptId}
          onImportComplete={handleImportComplete}
        />
      )}

      {/* Export Dialog */}
      {scriptId && (
        <ExportDialog
          open={showExport}
          onClose={() => setShowExport(false)}
          scriptId={scriptId}
          hasBranches={hasBranches}
        />
      )}

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

      {/* AI Chat Drawer — FAB bottom-right + slide-in panel.
          onApply writes the assistant content into whichever chapter
          node the user is currently editing. */}
      <AIChatDrawer
        projectId={projectId ?? ''}
        contextType="script"
        contextId={scriptId}
        onApplyContent={(content) => {
          const editingId = useScriptCanvasStore.getState().editingNodeId;
          if (editingId) {
            useScriptCanvasStore.getState().updateNodeData(editingId, { content });
          }
        }}
      />
    </div>
  );
}
