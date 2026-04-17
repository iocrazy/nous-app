// frontend/components/ResourcesView.tsx

/**
 * ResourcesView — public entry point.
 * Wraps ResourcesViewInner with the ResourcesProvider context.
 *
 * Heavy logic is split across:
 *   - ResourcesViewInner.tsx      — orchestration component
 *   - hooks/useResourceUpload.ts  — upload + drag-drop
 *   - hooks/useResourceOperations.ts — copy/move/rename/keyboard
 *   - BatchSelectionToolbar.tsx   — bottom multi-select toolbar
 */

import React from 'react';
import { ResourcesProvider } from '../contexts/ResourcesContext';
import { ResourcesViewInner } from './ResourcesViewInner';

// Re-export upload validation helper so external code still resolves it
export { validateFile } from '../hooks/useResourceUpload';

interface ResourcesViewProps {
  scopeType: 'personal' | 'team';
  scopeId: string;
}

export const ResourcesView: React.FC<ResourcesViewProps> = ({ scopeType, scopeId }) => {
  return (
    <ResourcesProvider scopeType={scopeType} scopeId={scopeId}>
      <ResourcesViewInner />
    </ResourcesProvider>
  );
};
