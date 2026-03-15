import React from 'react';
import { useStoryboardStore } from '../../stores/storyboardStore';

export function CanvasEditorPage() {
  const setCurrentProject = useStoryboardStore((s) => s.setCurrentProject);

  const handleBack = () => {
    setCurrentProject(null);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <button
          type="button"
          onClick={handleBack}
          className="flex items-center gap-1 text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors"
        >
          <span aria-hidden="true">←</span>
          Back
        </button>
        <span className="text-sm font-medium text-gray-900 dark:text-white">
          Canvas Editor
        </span>
      </div>
      <div className="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
        {/* Canvas editor will be rendered here */}
        Canvas Editor
      </div>
    </div>
  );
}
