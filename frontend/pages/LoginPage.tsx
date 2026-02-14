import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { LandingPage } from '../components/LandingPage';
import { AuthOverlay } from '../components/AuthOverlay';

export function LoginPage() {
  const { isAuthenticated, showAuthModal, setShowAuthModal, handleLogin } = useAuth();

  if (isAuthenticated) {
    return <Navigate to="/parser" replace />;
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
