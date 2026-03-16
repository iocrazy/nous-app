import { createBrowserRouter, Navigate, useLocation } from 'react-router-dom';
import { AuthGuard } from './components/AuthGuard';
import { AppLayout } from './components/AppLayout';
import { ModuleGuard } from './components/ModuleGuard';
import { RedirectToTeam, RedirectToDefaultTeam } from './components/RedirectToTeam';
import { LoginPage } from './pages/LoginPage';
import { ParserPage } from './pages/ParserPage';
import { LibraryPage } from './pages/LibraryPage';
import { DashboardPage } from './pages/DashboardPage';
import { SettingsPage } from './pages/SettingsPage';
import { CleanupPage } from './pages/CleanupPage';
import { ProjectsPage } from './pages/ProjectsPage';
import { PointsPage } from './pages/PointsPage';
import { BillingPage } from './pages/BillingPage';
import { MembersPage } from './pages/MembersPage';
import { ResourcesPage } from './pages/ResourcesPage';
import { TodolistPage } from './pages/TodolistPage';
import { PlayerPage } from './pages/PlayerPage';
import { ResourceDetailPage } from './pages/ResourceDetailPage';
import { SharePage } from './pages/SharePage';
import { SharedPage } from './pages/SharedPage';
import { ShortcutsTagsPage } from './pages/ShortcutsTagsPage';
import { StoryboardWorkbench } from './pages/StoryboardWorkbench';

export const router = createBrowserRouter([
  // Public routes (no auth required)
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/share/:shareCode',
    element: <SharePage />,
  },
  {
    path: '/shortcuts/tags',
    element: <ShortcutsTagsPage />,
  },

  // Authenticated routes
  {
    element: <AuthGuard />,
    children: [
      // Root → redirect to default team
      { index: true, element: <RedirectToDefaultTeam /> },

      // Legacy flat URLs → redirect to /team/:teamId/:view
      { path: 'parser', element: <RedirectToTeam view="parser" /> },
      { path: 'library', element: <RedirectToTeam view="library" /> },
      { path: 'library/:itemId', element: <RedirectToTeam view="library" /> },
      { path: 'dashboard', element: <RedirectToTeam view="dashboard" /> },
      { path: 'dashboard/:subview', element: <RedirectToTeam view="dashboard" /> },
      { path: 'resources', element: <RedirectToTeam view="resources" /> },
      { path: 'resources/file/:resourceId', element: <RedirectToTeam view="resources" /> },
      { path: 'resources/:section', element: <RedirectToTeam view="resources" /> },
      { path: 'projects', element: <RedirectToTeam view="projects" /> },
      { path: 'projects/:projectId', element: <RedirectToTeam view="projects" /> },
      { path: 'projects/:projectId/review/:fileId', element: <RedirectToTeam view="projects" /> },
      { path: 'points', element: <RedirectToTeam view="points" /> },
      { path: 'members', element: <RedirectToTeam view="members" /> },
      { path: 'billing', element: <RedirectToTeam view="billing" /> },
      { path: 'todolist', element: <RedirectToTeam view="todolist" /> },
      { path: 'cleanup', element: <RedirectToTeam view="cleanup" /> },
      { path: 'player/:displayId', element: <RedirectToTeam view="player" /> },
      { path: 'shared', element: <RedirectToTeam view="shared" /> },

      // Settings is account-level (no team scope)
      { path: 'settings', element: <AppLayout />, children: [
        { index: true, element: <SettingsPage /> },
      ]},

      // Team-scoped routes
      {
        path: 'team/:teamId',
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="parser" replace /> },
          { path: 'parser', element: <ModuleGuard moduleKey="parser"><ParserPage /></ModuleGuard> },
          { path: 'library', element: <ModuleGuard moduleKey="library"><LibraryPage /></ModuleGuard> },
          { path: 'library/:itemId', element: <ModuleGuard moduleKey="library"><LibraryPage /></ModuleGuard> },
          { path: 'dashboard', element: <ModuleGuard moduleKey="dashboard"><DashboardPage /></ModuleGuard> },
          { path: 'dashboard/:subview', element: <ModuleGuard moduleKey="dashboard"><DashboardPage /></ModuleGuard> },
          { path: 'resources', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'resources/file/:resourceId', element: <ModuleGuard moduleKey="resources"><ResourceDetailPage /></ModuleGuard> },
          { path: 'resources/:section', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'resources/folder/:folderId', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'resources/smart/:smartFolderId', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'resources/library/:libraryId', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'resources/library/:libraryId/folder/:folderId', element: <ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard> },
          { path: 'projects', element: <ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard> },
          { path: 'projects/:projectId', element: <ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard> },
          { path: 'projects/:projectId/review/:fileId', element: <ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard> },
          { path: 'settings', element: <SettingsPage /> },
          { path: 'cleanup', element: <ModuleGuard moduleKey="cleanup"><CleanupPage /></ModuleGuard> },
          { path: 'points', element: <PointsPage /> },
          { path: 'members', element: <MembersPage /> },
          { path: 'billing', element: <BillingPage /> },
          { path: 'todolist', element: <TodolistPage /> },
          { path: 'shared', element: <SharedPage /> },
          { path: 'player/:displayId', element: <PlayerPage /> },
          { path: 'storyboard', element: <ModuleGuard moduleKey="storyboard"><StoryboardWorkbench /></ModuleGuard> },
        ],
      },

      // Catch-all → redirect to default team
      { path: '*', element: <RedirectToDefaultTeam /> },
    ],
  },
]);
