import React, { useState, useEffect, useCallback } from 'react';
import { ChevronLeft, Pencil, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { useStoryboardStore } from '../../stores/storyboardStore';
import { fetchProject, updateProject } from '../../services/storyboardService';
import { useStoryboardPersist } from '../../hooks/storyboard/useStoryboardPersist';
import { useStoryboardRealtime } from '../../hooks/storyboard/useStoryboardRealtime';
import StoryboardCanvas from '../../components/storyboard/canvas/StoryboardCanvas';
import ChatPanel from '../../components/storyboard/chat/ChatPanel';
import FrameTimeline from '../../components/storyboard/timeline/FrameTimeline';
import CharacterPanel from '../../components/storyboard/characters/CharacterPanel';
import { StoryboardFrame } from '../../types';

// ─── Component ────────────────────────────────────────────────────────────────

export function CanvasEditorPage() {
  const { t } = useTranslation();
  const {
    currentProjectId,
    setCurrentProject,
    setNodes,
    setEdges,
    setCharacters,
    nodes,
  } = useStoryboardStore();

  // ─── Panel visibility ──────────────────────────────────────────────────────
  const [chatOpen, setChatOpen] = useState(true);
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [characterPanelOpen, setCharacterPanelOpen] = useState(false);

  // ─── Project info ──────────────────────────────────────────────────────────
  const [projectName, setProjectName] = useState('');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ─── Frames derived from nodes ─────────────────────────────────────────────
  const [frames, setFrames] = useState<StoryboardFrame[]>([]);
  const [selectedFrameId, setSelectedFrameId] = useState<string | null>(null);

  // ─── Hooks ─────────────────────────────────────────────────────────────────
  useStoryboardPersist();
  useStoryboardRealtime();

  // ─── Load project data on mount ────────────────────────────────────────────
  useEffect(() => {
    if (!currentProjectId) return;

    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchProject(currentProjectId!);
        if (cancelled) return;

        setProjectName(data.name);
        setNameInput(data.name);
        setNodes(data.nodes);
        setEdges(data.edges);
        setCharacters(data.characters);
      } catch (err) {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setError(message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => { cancelled = true; };
  }, [currentProjectId, setNodes, setEdges, setCharacters]);

  // Derive frames from image-type nodes that have frame data
  useEffect(() => {
    const derived: StoryboardFrame[] = nodes
      .filter((n) => {
        const data = n.data_json as Record<string, unknown> | undefined;
        return data?.frame != null;
      })
      .map((n) => (n.data_json as Record<string, unknown>).frame as StoryboardFrame)
      .sort((a, b) => a.sort_order - b.sort_order);
    setFrames(derived);
  }, [nodes]);

  // ─── Navigation ────────────────────────────────────────────────────────────
  const navigate = useNavigate();
  const { teamId } = useParams<{ teamId: string }>();

  const handleBack = useCallback(() => {
    setCurrentProject(null);
    navigate(`/team/${teamId}/storyboard`);
  }, [setCurrentProject, navigate, teamId]);

  // ─── Project name editing ──────────────────────────────────────────────────
  const handleNameSubmit = useCallback(async () => {
    if (!currentProjectId || !nameInput.trim() || nameInput.trim() === projectName) {
      setEditingName(false);
      setNameInput(projectName);
      return;
    }
    try {
      await updateProject(currentProjectId, { name: nameInput.trim() });
      setProjectName(nameInput.trim());
    } catch {
      setNameInput(projectName);
    } finally {
      setEditingName(false);
    }
  }, [currentProjectId, nameInput, projectName]);

  const handleNameKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter') handleNameSubmit();
      if (e.key === 'Escape') {
        setEditingName(false);
        setNameInput(projectName);
      }
    },
    [handleNameSubmit, projectName]
  );

  // ─── Timeline handlers ─────────────────────────────────────────────────────
  const handleSelectFrame = useCallback((frameId: string) => {
    setSelectedFrameId(frameId);
  }, []);

  const handleReorderFrames = useCallback((_reordered: StoryboardFrame[]) => {
    // Frame reordering is persisted by useStoryboardPersist via syncCanvas
    setFrames(_reordered);
  }, []);

  // ─── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="h-screen flex items-center justify-center bg-gray-950">
        <Loader2 size={32} className="animate-spin text-blue-500" />
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-gray-950 overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-2.5 border-b border-gray-800 flex-shrink-0 bg-gray-900">
        <button
          type="button"
          onClick={handleBack}
          className="flex items-center gap-1.5 text-sm text-gray-400 hover:text-gray-100 transition-colors"
        >
          <ChevronLeft size={16} />
          <span className="hidden sm:inline">{t('storyboard.back', 'Back')}</span>
        </button>

        <div className="w-px h-4 bg-gray-700" />

        {/* Editable project name */}
        {editingName ? (
          <input
            type="text"
            value={nameInput}
            autoFocus
            onChange={(e) => setNameInput(e.target.value)}
            onBlur={handleNameSubmit}
            onKeyDown={handleNameKeyDown}
            className="px-2 py-0.5 bg-gray-800 border border-blue-500 rounded text-sm font-medium text-gray-100 focus:outline-none min-w-0 max-w-xs"
          />
        ) : (
          <button
            type="button"
            onClick={() => { setEditingName(true); setNameInput(projectName); }}
            className="flex items-center gap-1.5 text-sm font-medium text-gray-200 hover:text-white transition-colors group"
          >
            <span className="truncate max-w-xs">{projectName}</span>
            <Pencil size={12} className="opacity-0 group-hover:opacity-60 transition-opacity flex-shrink-0" />
          </button>
        )}

        {/* Error indicator */}
        {error && (
          <span className="text-xs text-red-400 ml-2 truncate">{error}</span>
        )}
      </div>

      {/* Main layout */}
      <div className="flex-1 flex overflow-hidden min-h-0">
        {/* Character Panel (left slide-out) */}
        <CharacterPanel
          open={characterPanelOpen}
          onClose={() => setCharacterPanelOpen(false)}
        />

        {/* Canvas (center) */}
        <div className="flex-1 relative overflow-hidden">
          <StoryboardCanvas />
        </div>

        {/* Chat Panel (right, collapsible) */}
        <ChatPanel
          open={chatOpen}
          onToggle={() => setChatOpen((v) => !v)}
        />
      </div>

      {/* Frame Timeline (bottom, collapsible) */}
      <FrameTimeline
        frames={frames}
        selectedFrameId={selectedFrameId}
        onSelectFrame={handleSelectFrame}
        onReorderFrames={handleReorderFrames}
      />
    </div>
  );
}
