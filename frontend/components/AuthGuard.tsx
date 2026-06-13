import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

export function AuthGuard() {
  const { isAuthenticated, isAuthLoading } = useAuth();
  const location = useLocation();

  // Wait for session check before deciding
  if (isAuthLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-black">
        <div className="w-8 h-8 rounded-full border-2 border-ink-700 border-t-indigo-400 animate-spin" />
      </div>
    );
  }

  if (!isAuthenticated) {
    // Preserve original URL so LoginPage can redirect back
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
