import { useState, useCallback } from 'react';
import { ChevronLeft, Pencil } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { ReactFlowProvider } from '@xyflow/react';
import { Canvas } from '../../features/storyboard/Canvas';

export function CanvasEditorPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { teamId, projectId } = useParams<{ teamId: string; projectId?: string }>();

  const [projectName, setProjectName] = useState('Untitled Project');
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');

  const handleBack = useCallback(() => {
    navigate(`/team/${teamId}/storyboard`);
  }, [navigate, teamId]);

  const handleNameSubmit = useCallback(() => {
    if (!nameInput.trim() || nameInput.trim() === projectName) {
      setEditingName(false);
      setNameInput(projectName);
      return;
    }
    setProjectName(nameInput.trim());
    setEditingName(false);
  }, [nameInput, projectName]);

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

  return (
    <div className="h-full flex flex-col bg-gray-950 overflow-hidden">
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
      </div>

      {/* Canvas */}
      <div className="flex-1 relative overflow-hidden min-h-0">
        <ReactFlowProvider>
          <Canvas />
        </ReactFlowProvider>
      </div>
    </div>
  );
}
