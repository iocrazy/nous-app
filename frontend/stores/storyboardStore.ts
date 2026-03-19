// Minimal storyboard project store — manages project selection and list state.
// The old storyboard canvas state (nodes, edges, characters) has been migrated to canvasStore.

import { create } from 'zustand';
import type { ProjectSummary } from '../types';

interface StoryboardStoreState {
  currentProjectId: string | null;
  projectList: ProjectSummary[];
  setCurrentProject: (id: string | null) => void;
  setProjectList: (projects: ProjectSummary[]) => void;
}

export const useStoryboardStore = create<StoryboardStoreState>((set) => ({
  currentProjectId: null,
  projectList: [],
  setCurrentProject: (id) => set({ currentProjectId: id }),
  setProjectList: (projects) => set({ projectList: projects }),
}));
