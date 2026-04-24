import React from 'react';
import { Navigate, useParams } from 'react-router-dom';

/**
 * Default landing for /ai-library (and /team/:teamId/ai-library).
 *
 * Redirects to the agents list so the secondary sidebar has something
 * meaningful selected on first visit.
 */
export const AILibraryIndex: React.FC = () => {
  const { teamId } = useParams();
  const prefix = teamId ? `/team/${teamId}` : '';
  return <Navigate to={`${prefix}/ai-library/agents`} replace />;
};

export default AILibraryIndex;
