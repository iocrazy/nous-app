import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AuthGuard } from './components/AuthGuard';
import { AppLayout } from './components/AppLayout';
import { ModuleGuard } from './components/ModuleGuard';
import { RedirectToTeam, RedirectToDefaultTeam } from './components/RedirectToTeam';

// Eagerly loaded (needed immediately)
import { LoginPage } from './pages/LoginPage';

// Lazy-loaded pages (code-split per route)
const ParserPage = lazy(() => import('./pages/ParserPage').then(m => ({ default: m.ParserPage })));
const DashboardPage = lazy(() => import('./pages/DashboardPage').then(m => ({ default: m.DashboardPage })));
const SettingsPage = lazy(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })));
const CleanupPage = lazy(() => import('./pages/CleanupPage').then(m => ({ default: m.CleanupPage })));
const ProjectsPage = lazy(() => import('./pages/ProjectsPage').then(m => ({ default: m.ProjectsPage })));
const PointsPage = lazy(() => import('./pages/PointsPage').then(m => ({ default: m.PointsPage })));
const BillingPage = lazy(() => import('./pages/BillingPage').then(m => ({ default: m.BillingPage })));
const MembersPage = lazy(() => import('./pages/MembersPage').then(m => ({ default: m.MembersPage })));
const ResourcesPage = lazy(() => import('./pages/ResourcesPage').then(m => ({ default: m.ResourcesPage })));
const TodolistPage = lazy(() => import('./pages/TodolistPage').then(m => ({ default: m.TodolistPage })));
const FileDetailDispatcher = lazy(() => import('./pages/FileDetailDispatcher').then(m => ({ default: m.FileDetailDispatcher })));
const SharePage = lazy(() => import('./pages/SharePage').then(m => ({ default: m.SharePage })));
const SharedPage = lazy(() => import('./pages/SharedPage').then(m => ({ default: m.SharedPage })));
const ShortcutsTagsPage = lazy(() => import('./pages/ShortcutsTagsPage').then(m => ({ default: m.ShortcutsTagsPage })));
const StoryboardWorkbench = lazy(() => import('./pages/StoryboardWorkbench').then(m => ({ default: m.StoryboardWorkbench })));
const ScriptEditor = lazy(() => import('./pages/ScriptEditor').then(m => ({ default: m.ScriptEditor })));
const DownloadDetailPage = lazy(() => import('./pages/DownloadDetailPage').then(m => ({ default: m.DownloadDetailPage })));
const AgentsPage = lazy(() => import('./pages/AgentsPage').then(m => ({ default: m.AgentsPage })));
const SkillsPage = lazy(() => import('./pages/SkillsPage').then(m => ({ default: m.SkillsPage })));

function PageLoader() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="w-6 h-6 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
    </div>
  );
}

function SuspenseWrap({ children }: { children: React.ReactNode }) {
  return <Suspense fallback={<PageLoader />}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  // Public routes (no auth required)
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/share/:shareCode',
    element: <SuspenseWrap><SharePage /></SuspenseWrap>,
  },
  {
    path: '/shortcuts/tags',
    element: <SuspenseWrap><ShortcutsTagsPage /></SuspenseWrap>,
  },

  // Authenticated routes
  {
    element: <AuthGuard />,
    children: [
      // Root → redirect to default team
      { index: true, element: <RedirectToDefaultTeam /> },

      // Legacy flat URLs → redirect to /team/:teamId/:view
      { path: 'parser', element: <RedirectToTeam view="parser" /> },
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
      { path: 'shared', element: <RedirectToTeam view="shared" /> },
      { path: 'agents', element: <RedirectToTeam view="agents" /> },
      { path: 'agents/:slug', element: <RedirectToTeam view="agents" /> },
      { path: 'skills', element: <RedirectToTeam view="skills" /> },
      { path: 'skills/:slug', element: <RedirectToTeam view="skills" /> },

      // Settings is account-level (no team scope)
      { path: 'settings', element: <AppLayout />, children: [
        { index: true, element: <SuspenseWrap><SettingsPage /></SuspenseWrap> },
      ]},

      // Team-scoped routes
      {
        path: 'team/:teamId',
        element: <AppLayout />,
        children: [
          { index: true, element: <Navigate to="parser" replace /> },
          { path: 'parser', element: <SuspenseWrap><ModuleGuard moduleKey="parser"><ParserPage /></ModuleGuard></SuspenseWrap> },
          { path: 'dashboard', element: <SuspenseWrap><ModuleGuard moduleKey="dashboard"><DashboardPage /></ModuleGuard></SuspenseWrap> },
          { path: 'dashboard/:subview', element: <SuspenseWrap><ModuleGuard moduleKey="dashboard"><DashboardPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/file/:resourceId', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><FileDetailDispatcher /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/:section', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/folder/:folderId', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/smart/:smartFolderId', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/library/:libraryId', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'resources/library/:libraryId/folder/:folderId', element: <SuspenseWrap><ModuleGuard moduleKey="resources"><ResourcesPage /></ModuleGuard></SuspenseWrap> },
          { path: 'projects', element: <SuspenseWrap><ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard></SuspenseWrap> },
          { path: 'projects/:projectId', element: <SuspenseWrap><ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard></SuspenseWrap> },
          { path: 'projects/:projectId/review/:fileId', element: <SuspenseWrap><ModuleGuard moduleKey="projects"><ProjectsPage /></ModuleGuard></SuspenseWrap> },
          { path: 'settings', element: <SuspenseWrap><SettingsPage /></SuspenseWrap> },
          { path: 'cleanup', element: <SuspenseWrap><ModuleGuard moduleKey="cleanup"><CleanupPage /></ModuleGuard></SuspenseWrap> },
          { path: 'points', element: <SuspenseWrap><PointsPage /></SuspenseWrap> },
          { path: 'members', element: <SuspenseWrap><MembersPage /></SuspenseWrap> },
          { path: 'billing', element: <SuspenseWrap><BillingPage /></SuspenseWrap> },
          { path: 'todolist', element: <SuspenseWrap><TodolistPage /></SuspenseWrap> },
          { path: 'shared', element: <SuspenseWrap><SharedPage /></SuspenseWrap> },
          { path: 'agents', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
          { path: 'agents/:slug', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
          { path: 'skills', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
          { path: 'skills/:slug', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
          { path: 'player/:displayId', element: <SuspenseWrap><DownloadDetailPage /></SuspenseWrap> },
          // Script & Storyboard editors handled by fullscreen routes below
        ],
      },

      // ── Fullscreen editor routes (outside AppLayout, still auth-guarded) ──
      {
        path: 'team/:teamId/projects/:projectId/storyboard/:storyboardId',
        element: <SuspenseWrap><ModuleGuard moduleKey="projects"><StoryboardWorkbench /></ModuleGuard></SuspenseWrap>,
      },
      {
        path: 'team/:teamId/projects/:projectId/scripts/:scriptId',
        element: <SuspenseWrap><ScriptEditor /></SuspenseWrap>,
      },

      // Catch-all → redirect to default team
      { path: '*', element: <RedirectToDefaultTeam /> },
    ],
  },
]);
