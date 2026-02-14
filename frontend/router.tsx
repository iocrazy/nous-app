import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AuthGuard } from './components/AuthGuard';
import { AppLayout } from './components/AppLayout';
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
      {
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="/parser" replace /> },
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
          { path: '*', element: <Navigate to="/parser" replace /> },
        ],
      },
    ],
  },
]);
