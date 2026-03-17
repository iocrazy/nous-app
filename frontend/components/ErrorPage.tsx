import { Lock, ShieldX, FileQuestion, AlertTriangle } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

interface ErrorPageProps {
  code: 401 | 403 | 404 | 500;
  onAction?: () => void;
  onSecondaryAction?: () => void;
  className?: string;
}

const ERROR_CONFIG = {
  401: {
    icon: Lock,
    title: 'Sign in to continue',
    description: 'You need to be signed in to view this content.',
    cta: 'Sign In',
  },
  403: {
    icon: ShieldX,
    title: 'Access restricted',
    description: "You don't have permission to view this content.",
    cta: 'Go Home',
  },
  404: {
    icon: FileQuestion,
    title: 'Page not found',
    description: "This page doesn't exist or has been removed.",
    cta: 'Go Home',
  },
  500: {
    icon: AlertTriangle,
    title: 'Something went wrong',
    description: 'An unexpected error occurred. Please try again.',
    cta: 'Retry',
  },
} as const;

export default function ErrorPage({ code, onAction, onSecondaryAction, className = '' }: ErrorPageProps) {
  const { isAuthenticated, setShowAuthModal } = useAuth();

  // Security: unauthenticated users see 401 for both 403 and 404
  const effectiveCode = !isAuthenticated && (code === 403 || code === 404) ? 401 : code;
  const config = ERROR_CONFIG[effectiveCode];
  const Icon = config.icon;

  const handleAction = () => {
    if (effectiveCode === 401) {
      setShowAuthModal(true);
    } else if (onAction) {
      onAction();
    } else {
      window.location.href = '/';
    }
  };

  return (
    <div className={`flex items-center justify-center min-h-[400px] ${className}`}>
      <div className="text-center max-w-md px-6">
        <div className="flex justify-center mb-6">
          <div className="w-16 h-16 rounded-full bg-zinc-800/50 flex items-center justify-center">
            <Icon className="w-8 h-8 text-zinc-400" />
          </div>
        </div>
        <h2 className="text-xl font-semibold text-zinc-100 mb-2">
          {config.title}
        </h2>
        <p className="text-zinc-400 text-sm mb-6">
          {config.description}
        </p>
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={handleAction}
            className="px-5 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
          >
            {config.cta}
          </button>
          {effectiveCode === 500 && onSecondaryAction && (
            <button
              onClick={onSecondaryAction}
              className="px-5 py-2 bg-zinc-700 hover:bg-zinc-600 text-zinc-200 text-sm font-medium rounded-lg transition-colors"
            >
              Go Home
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
