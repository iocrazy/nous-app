import React, { Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import './index.css';
import './i18n';

// Patch DOM methods to prevent "removeChild" errors caused by browser
// extensions (translators, Grammarly, etc.) that modify React-managed DOM nodes.
// See: https://github.com/facebook/react/issues/11538
if (typeof Node !== 'undefined') {
  const origRemoveChild = Node.prototype.removeChild;
  Node.prototype.removeChild = function <T extends Node>(child: T): T {
    if (child.parentNode !== this) {
      console.warn('[DOM Patch] removeChild: node is not a child of this parent');
      return child;
    }
    return origRemoveChild.call(this, child) as T;
  };

  const origInsertBefore = Node.prototype.insertBefore;
  Node.prototype.insertBefore = function <T extends Node>(newNode: T, refNode: Node | null): T {
    if (refNode && refNode.parentNode !== this) {
      console.warn('[DOM Patch] insertBefore: reference node is not a child of this parent');
      return newNode;
    }
    return origInsertBefore.call(this, newNode, refNode) as T;
  };
}
import { router } from './router';
import { AuthProvider } from './contexts/AuthContext';
import { TeamProvider } from './contexts/TeamContext';
import PWAUpdatePrompt from './components/PWAUpdatePrompt';
import { ErrorBoundary } from './components/ErrorBoundary';
import { installErrorReporter } from './services/errorReporter';

// Capture window.onerror + unhandledrejection into frontend_error_logs.
installErrorReporter();

// Loading component for i18n lazy loading
const LoadingFallback = () => (
  <div className="min-h-screen bg-black flex items-center justify-center">
    <div className="flex flex-col items-center gap-4">
      <div className="w-8 h-8 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
      <span className="text-zinc-500 text-sm">Loading...</span>
    </div>
  </div>
);

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <ErrorBoundary>
      <Suspense fallback={<LoadingFallback />}>
        <AuthProvider>
          <TeamProvider>
            <RouterProvider router={router} />
          </TeamProvider>
        </AuthProvider>
        <PWAUpdatePrompt />
      </Suspense>
    </ErrorBoundary>
  </React.StrictMode>
);
