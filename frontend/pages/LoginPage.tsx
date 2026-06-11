import { useState } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { Clock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
import { LandingPage } from '../components/LandingPage';
import { AuthOverlay } from '../components/AuthOverlay';
import { ClockDriftBanner } from '../components/ClockDriftBanner';

/** One-shot read of the breadcrumb authRecovery leaves when it kicks a
 * dead session to /login — lets us tell the user WHY they're here. */
function consumeAuthExpiredFlag(): boolean {
  try {
    const flag = sessionStorage.getItem('mediahub_auth_expired') === '1';
    sessionStorage.removeItem('mediahub_auth_expired');
    return flag;
  } catch {
    return false;
  }
}

export function LoginPage() {
  const { isAuthenticated, showAuthModal, setShowAuthModal, handleLogin } = useAuth();
  const location = useLocation();
  const { t } = useTranslation();
  const [sessionExpired] = useState(consumeAuthExpiredFlag);

  // Redirect to the page user originally wanted, or /parser as default
  const from = (location.state as { from?: string })?.from || '/parser';

  if (isAuthenticated) {
    return <Navigate to={from} replace />;
  }

  return (
    <>
      <ClockDriftBanner />
      {sessionExpired && (
        <div className="fixed top-0 inset-x-0 z-[60] flex items-center justify-center gap-2 px-4 py-2 bg-amber-500/15 border-b border-amber-500/30 text-amber-300 text-xs">
          <Clock size={13} className="shrink-0" />
          {t('auth.sessionExpired', 'Your session expired — please sign in again.')}
        </div>
      )}
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
