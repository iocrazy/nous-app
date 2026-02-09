import React, { Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import './i18n';
import App from './App';
import PWAUpdatePrompt from './components/PWAUpdatePrompt';

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
    <Suspense fallback={<LoadingFallback />}>
      <App />
      <PWAUpdatePrompt />
    </Suspense>
  </React.StrictMode>
);
