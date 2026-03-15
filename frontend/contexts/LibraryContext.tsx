import React, { createContext, useContext } from 'react';
import { useAuth } from './AuthContext';
import { useTeamContext } from './TeamContext';
import { useLibrary } from '../hooks/useLibrary';

type LibraryContextValue = ReturnType<typeof useLibrary>;

const LibraryContext = createContext<LibraryContextValue | null>(null);

export function LibraryProvider({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  const { selectedTeamId } = useTeamContext();

  const library = useLibrary({
    isAuthenticated,
    selectedTeamId,
  });

  return (
    <LibraryContext.Provider value={library}>
      {children}
    </LibraryContext.Provider>
  );
}

export function useLibraryContext(): LibraryContextValue {
  const ctx = useContext(LibraryContext);
  if (!ctx) throw new Error('useLibraryContext must be used within LibraryProvider');
  return ctx;
}
