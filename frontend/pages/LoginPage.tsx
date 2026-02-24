import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { LandingPage } from '../components/LandingPage';
import { AuthOverlay } from '../components/AuthOverlay';

export function LoginPage() {
  const { isAuthenticated, showAuthModal, setShowAuthModal, handleLogin } = useAuth();
  const location = useLocation();

  // Redirect to the page user originally wanted, or /parser as default
  const from = (location.state as { from?: string })?.from || '/parser';

  if (isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  return (
    <>
      <LandingPage
        onLoginClick={() => setShowAuthModal(true)}
        onGetStarted={() => setShowAuthModal(true)}
      />
      {showAuthModal && (
        <AuthOverlay
          onLogin={handleLogin}
          onClose={() => setShowAuthModal(false)}
        />
      )}
    </>
  );
}
