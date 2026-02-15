import { createBrowserRouter, Navigate, useLocation } from 'react-router-dom';
import { AuthGuard } from './components/AuthGuard';
import { AppLayout } from './components/AppLayout';
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

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    element: <AuthGuard />,
    children: [
      // Root → redirect to default team
      { index: true, element: <RedirectToDefaultTeam /> },

      // Legacy flat URLs → redirect to /t/:teamId/:view
      { path: 'parser', element: <RedirectToTeam view="parser" /> },
      { path: 'library', element: <RedirectToTeam view="library" /> },
      { path: 'library/:itemId', element: <RedirectToTeam view="library" /> },
      { path: 'dashboard', element: <RedirectToTeam view="dashboard" /> },
      { path: 'dashboard/:subview', element: <RedirectToTeam view="dashboard" /> },
      { path: 'resources', element: <RedirectToTeam view="resources" /> },
      { path: 'resources/:section', element: <RedirectToTeam view="resources" /> },
      { path: 'projects', element: <RedirectToTeam view="projects" /> },
      { path: 'projects/:projectId', element: <RedirectToTeam view="projects" /> },
      { path: 'projects/:projectId/review/:fileId', element: <RedirectToTeam view="projects" /> },
      { path: 'points', element: <RedirectToTeam view="points" /> },
      { path: 'members', element: <RedirectToTeam view="members" /> },
      { path: 'billing', element: <RedirectToTeam view="billing" /> },
      { path: 'todolist', element: <RedirectToTeam view="todolist" /> },
      { path: 'cleanup', element: <RedirectToTeam view="cleanup" /> },

      // Settings is account-level (no team scope)
      { path: 'settings', element: <AppLayout />, children: [
        { index: true, element: <SettingsPage /> },
      ]},

      // Team-scoped routes
      {
        path: 't/:teamId',
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="parser" replace /> },
          { path: 'parser', element: <ParserPage /> },
          { path: 'library', element: <LibraryPage /> },
          { path: 'library/:itemId', element: <LibraryPage /> },
          { path: 'dashboard', element: <DashboardPage /> },
          { path: 'dashboard/:subview', element: <DashboardPage /> },
          { path: 'resources', element: <ResourcesPage /> },
          { path: 'resources/:section', element: <ResourcesPage /> },
          { path: 'resources/folder/:folderId', element: <ResourcesPage /> },
          { path: 'resources/smart/:smartFolderId', element: <ResourcesPage /> },
          { path: 'projects', element: <ProjectsPage /> },
          { path: 'projects/:projectId', element: <ProjectsPage /> },
          { path: 'projects/:projectId/review/:fileId', element: <ProjectsPage /> },
          { path: 'settings', element: <SettingsPage /> },
          { path: 'cleanup', element: <CleanupPage /> },
          { path: 'points', element: <PointsPage /> },
          { path: 'members', element: <MembersPage /> },
          { path: 'billing', element: <BillingPage /> },
          { path: 'todolist', element: <TodolistPage /> },
        ],
      },

      // Catch-all → redirect to default team
      { path: '*', element: <RedirectToDefaultTeam /> },
    ],
  },
]);
