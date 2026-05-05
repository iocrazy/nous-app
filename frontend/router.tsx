import { lazy, Suspense, type LazyExoticComponent } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AuthGuard } from './components/AuthGuard';
import { AppLayout } from './components/AppLayout';
import { ModuleGuard } from './components/ModuleGuard';
import { RedirectToTeam, RedirectToDefaultTeam } from './components/RedirectToTeam';

// Eagerly loaded (needed immediately)
import { LoginPage } from './pages/LoginPage';

// Stale-chunk auto-reload: when a deploy ships and the user's session
// holds onto an HTML referencing now-deleted chunk hashes, ``import()``
// rejects with "Failed to fetch dynamically imported module". Catch
// that, force a one-time reload (sessionStorage sentinel prevents an
// infinite loop on real network errors), and surface the error
// otherwise. This makes deploys self-healing for users who don't hard
// refresh.
const STALE_CHUNK_RELOADED_KEY = 'mh_stale_chunk_reloaded';
function lazyWithRetry<T extends { default: any }>(
  factory: () => Promise<T>,
): LazyExoticComponent<any> {
  return lazy(() =>
    factory().catch((err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      const isStaleChunk =
        /Failed to fetch dynamically imported module/i.test(msg) ||
        /Loading chunk \d+ failed/i.test(msg) ||
        /error loading dynamically imported module/i.test(msg);
      if (isStaleChunk && typeof window !== 'undefined') {
        const reloaded = sessionStorage.getItem(STALE_CHUNK_RELOADED_KEY);
        if (!reloaded) {
          sessionStorage.setItem(STALE_CHUNK_RELOADED_KEY, String(Date.now()));
          window.location.reload();
          // Never-resolving promise so React stays on the loader instead
          // of flashing the error UI before the reload kicks in.
          return new Promise<T>(() => {});
        }
      }
      throw err;
    }),
  );
}

// Lazy-loaded pages (code-split per route)
const ParserPage = lazyWithRetry(() => import('./pages/ParserPage').then(m => ({ default: m.ParserPage })));
const DashboardPage = lazyWithRetry(() => import('./pages/DashboardPage').then(m => ({ default: m.DashboardPage })));
const SettingsPage = lazyWithRetry(() => import('./pages/SettingsPage').then(m => ({ default: m.SettingsPage })));
const CleanupPage = lazyWithRetry(() => import('./pages/CleanupPage').then(m => ({ default: m.CleanupPage })));
const ProjectsPage = lazyWithRetry(() => import('./pages/ProjectsPage').then(m => ({ default: m.ProjectsPage })));
const PointsPage = lazyWithRetry(() => import('./pages/PointsPage').then(m => ({ default: m.PointsPage })));
const BillingPage = lazyWithRetry(() => import('./pages/BillingPage').then(m => ({ default: m.BillingPage })));
const MembersPage = lazyWithRetry(() => import('./pages/MembersPage').then(m => ({ default: m.MembersPage })));
const ResourcesPage = lazyWithRetry(() => import('./pages/ResourcesPage').then(m => ({ default: m.ResourcesPage })));
const TodolistPage = lazyWithRetry(() => import('./pages/TodolistPage').then(m => ({ default: m.TodolistPage })));
const FileDetailDispatcher = lazyWithRetry(() => import('./pages/FileDetailDispatcher').then(m => ({ default: m.FileDetailDispatcher })));
const SharePage = lazyWithRetry(() => import('./pages/SharePage').then(m => ({ default: m.SharePage })));
const SharedPage = lazyWithRetry(() => import('./pages/SharedPage').then(m => ({ default: m.SharedPage })));
const ShortcutsTagsPage = lazyWithRetry(() => import('./pages/ShortcutsTagsPage').then(m => ({ default: m.ShortcutsTagsPage })));
const StoryboardWorkbench = lazyWithRetry(() => import('./pages/StoryboardWorkbench').then(m => ({ default: m.StoryboardWorkbench })));
const ScriptEditor = lazyWithRetry(() => import('./pages/ScriptEditor').then(m => ({ default: m.ScriptEditor })));
const DownloadDetailPage = lazyWithRetry(() => import('./pages/DownloadDetailPage').then(m => ({ default: m.DownloadDetailPage })));
const AgentsPage = lazyWithRetry(() => import('./pages/AgentsPage').then(m => ({ default: m.AgentsPage })));
const SkillsPage = lazyWithRetry(() => import('./pages/SkillsPage').then(m => ({ default: m.SkillsPage })));
const UsagePage = lazyWithRetry(() => import('./pages/UsagePage').then(m => ({ default: m.UsagePage })));
const WorkforcePage = lazyWithRetry(() => import('./pages/WorkforcePage').then(m => ({ default: m.WorkforcePage })));
const MemoryViewerPage = lazyWithRetry(() => import('./pages/MemoryViewerPage').then(m => ({ default: m.MemoryViewerPage })));
const IssuesPage = lazyWithRetry(() => import('./pages/IssuesPage').then(m => ({ default: m.IssuesPage })));
const AILibraryLayout = lazyWithRetry(() =>
  import('./components/AILibrary/AILibraryLayout').then(m => ({ default: m.AILibraryLayout })),
);
const AILibraryIndex = lazyWithRetry(() =>
  import('./pages/AILibraryIndex').then(m => ({ default: m.AILibraryIndex })),
);

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
      { path: 'issues', element: <RedirectToTeam view="issues" /> },
      { path: 'issues/:identifier', element: <RedirectToTeam view="issues" /> },
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
          { path: 'todolist/:identifier', element: <SuspenseWrap><TodolistPage /></SuspenseWrap> },
          { path: 'issues', element: <SuspenseWrap><IssuesPage /></SuspenseWrap> },
          { path: 'issues/:identifier', element: <SuspenseWrap><IssuesPage /></SuspenseWrap> },
          { path: 'shared', element: <SuspenseWrap><SharedPage /></SuspenseWrap> },
          // Legacy AI Library routes (kept for bookmark compatibility —
          // render without the new secondary sidebar).
          { path: 'agents', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
          { path: 'agents/:slug', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
          { path: 'skills', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
          { path: 'skills/:slug', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
          { path: 'usage', element: <SuspenseWrap><UsagePage /></SuspenseWrap> },

          // New nested AI Library — secondary sidebar + editor pane.
          {
            path: 'ai-library',
            element: <SuspenseWrap><AILibraryLayout /></SuspenseWrap>,
            children: [
              { index: true, element: <SuspenseWrap><AILibraryIndex /></SuspenseWrap> },
              { path: 'agents', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
              { path: 'agents/:slug', element: <SuspenseWrap><AgentsPage /></SuspenseWrap> },
              { path: 'skills', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
              { path: 'skills/:slug', element: <SuspenseWrap><SkillsPage /></SuspenseWrap> },
              {
                // Splat captures the full ``<relpath>`` after ``/files/`` so
                // that paths like ``references/examples.md`` resolve.
                path: 'skills/:slug/files/*',
                element: <SuspenseWrap><SkillsPage /></SuspenseWrap>,
              },
              { path: 'usage', element: <SuspenseWrap><UsagePage /></SuspenseWrap> },
              { path: 'workforce', element: <SuspenseWrap><WorkforcePage /></SuspenseWrap> },
              { path: 'memory', element: <SuspenseWrap><MemoryViewerPage /></SuspenseWrap> },
            ],
          },
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
