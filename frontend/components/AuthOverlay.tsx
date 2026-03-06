
import React, { useState } from 'react';
import {
  X, Smartphone, Mail, Eye, EyeOff, AlertCircle, Play, Download, Shield, Zap
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getSupabaseClient, isSupabaseConfigured } from '../supabaseClient';

interface AuthOverlayProps {
  onLogin: (user: { email: string; id: string }) => void;
  onClose: () => void;
}

// Simple Google Logo SVG
const GoogleIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" xmlns="http://www.w3.org/2000/svg">
    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/>
    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
  </svg>
);

// WeChat Logo SVG
const WeChatIcon = () => (
  <svg viewBox="0 0 24 24" width="22" height="22" fill="#07C160" xmlns="http://www.w3.org/2000/svg">
     <path d="M8.5,2C4.9,2,2,4.7,2,8c0,1.9,1,3.7,2.7,4.9l-0.7,2.1l2.4-1.3c0.7,0.2,1.4,0.3,2.1,0.3c0.2,0,0.5,0,0.7,0 c-0.2-0.6-0.2-1.3-0.2-2c0-3.9,3.6-7,8-7c0.5,0,1,0,1.5,0.1C16.8,3.2,12.9,2,8.5,2z M17,6c-3.9,0-7,2.7-7,6c0,3.3,3.1,6,7,6 c0.7,0,1.3-0.1,2-0.3l1.9,1l-0.6-1.8c1.5-1,2.5-2.6,2.5-4.3C23,9,20.2,6,17,6z M15,10c0.6,0,1,0.4,1,1s-0.4,1-1,1s-1-0.4-1-1 S14.4,10,15,10z M19,10c0.6,0,1,0.4,1,1s-0.4,1-1,1s-1-0.4-1-1S18.4,10,19,10z"/>
  </svg>
);

// Douyin Logo SVG (Musical Note)
const DouyinIcon = () => (
    <svg viewBox="0 0 24 24" width="20" height="20" fill="#000000" xmlns="http://www.w3.org/2000/svg">
        <path d="M19.5 5.7c-1.3.3-2.6.9-3.6 1.8V17c0 3.3-2.7 6-6 6s-6-2.7-6-6 2.7-6 6-6c.5 0 .9.1 1.4.2V14.3c-.4-.1-.9-.2-1.4-.2-1.6 0-2.9 1.3-2.9 2.9s1.3 2.9 2.9 2.9 2.9-1.3 2.9-2.9V2h3.1c0 .8.3 1.6.9 2.1.8.6 1.7.9 2.8.7z"/>
    </svg>
);


export const AuthOverlay: React.FC<AuthOverlayProps> = ({ onLogin, onClose }) => {
  const { t } = useTranslation();

  const [authMode, setAuthMode] = useState<'login' | 'register'>('login');
  const [loginMode, setLoginMode] = useState<'phone' | 'email'>('email');
  const [loading, setLoading] = useState(false);

  // Input states
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Style object to enforce visibility
  const inputStyle = { backgroundColor: 'white', color: 'black' };

  // Convert phone number to deterministic email for Supabase auth
  const phoneToEmail = (phoneNumber: string): string => {
    const cleaned = phoneNumber.replace(/\D/g, '');
    return `86_${cleaned}@phone.mediahub.internal`;
  };

  const isValidPhone = (phoneNumber: string): boolean => {
    const cleaned = phoneNumber.replace(/\D/g, '');
    return /^1[3-9]\d{9}$/.test(cleaned);
  };

  const handleLogin = async () => {
    setError(null);

    if (loginMode === 'phone') {
      if (!phone || !password) {
        setError('Please enter phone number and password');
        return;
      }
      if (!isValidPhone(phone)) {
        setError('Please enter a valid 11-digit mobile number');
        return;
      }
    } else {
      if (!email || !password) {
        setError('Please enter email and password');
        return;
      }
    }

    const supabase = getSupabaseClient();
    if (isSupabaseConfigured() && supabase) {
      setLoading(true);
      try {
        const loginEmail = loginMode === 'phone' ? phoneToEmail(phone) : email;
        const { data, error } = await supabase.auth.signInWithPassword({
          email: loginEmail,
          password,
        });
        if (error) {
          setError(loginMode === 'phone' && error.message === 'Invalid login credentials'
            ? 'Phone number or password is incorrect'
            : error.message);
          return;
        }
        if (data.user) {
          const displayEmail = loginMode === 'phone'
            ? (data.user.user_metadata?.phone || phone)
            : (data.user.email || email);
          onLogin({ email: displayEmail, id: data.user.id });
        }
      } catch (err: any) {
        setError(err.message || 'Login failed');
      } finally {
        setLoading(false);
      }
    } else {
      // Demo mode
      const demoEmail = loginMode === 'phone'
        ? (phone || 'demo@example.com')
        : (email || 'demo@example.com');
      onLogin({ email: demoEmail, id: 'demo-user' });
    }
  };

  const handleRegister = async () => {
    setError(null);

    if (loginMode === 'phone') {
      if (!phone || !password) {
        setError('Please enter phone number and password');
        return;
      }
      if (!isValidPhone(phone)) {
        setError('Please enter a valid 11-digit mobile number');
        return;
      }
    } else {
      if (!email || !password) {
        setError('Please enter email and password');
        return;
      }
    }

    if (password !== confirmPassword) {
      setError(t('auth.passwordMismatch'));
      return;
    }

    if (password.length < 6) {
      setError('Password must be at least 6 characters');
      return;
    }

    const supabase = getSupabaseClient();
    if (isSupabaseConfigured() && supabase) {
      setLoading(true);
      try {
        const signUpEmail = loginMode === 'phone' ? phoneToEmail(phone) : email;
        const metadata = loginMode === 'phone'
          ? { phone: `+86${phone.replace(/\D/g, '')}`, login_type: 'phone' }
          : undefined;

        const { data, error } = await supabase.auth.signUp({
          email: signUpEmail,
          password,
          options: metadata ? { data: metadata } : undefined,
        });
        if (error) {
          setError(error.message);
          return;
        }
        alert(t('auth.registerSuccess'));
        setAuthMode('login');
        setPassword('');
        setConfirmPassword('');
      } catch (err: any) {
        setError(err.message || 'Registration failed');
      } finally {
        setLoading(false);
      }
    } else {
      // Demo mode
      alert(t('auth.registerSuccess'));
      setAuthMode('login');
      setPassword('');
      setConfirmPassword('');
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-md z-[100] flex items-center justify-center p-4 animate-in fade-in duration-300">

      {/* Main Modal Container */}
      <div className="bg-white rounded-[20px] shadow-2xl flex flex-col md:flex-row w-full max-w-[800px] overflow-hidden relative animate-in zoom-in-95 duration-300">

        {/* Close Button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-zinc-400 hover:text-zinc-600 transition-colors z-20"
        >
          <X size={24} />
        </button>

        {/* Left Side: Brand & Features */}
        <div className="hidden md:flex w-[320px] bg-gradient-to-br from-indigo-600 via-purple-600 to-pink-500 p-8 flex-col items-center justify-center text-center relative overflow-hidden">
          {/* Background Pattern */}
          <div className="absolute inset-0 opacity-10">
            <div className="absolute top-10 left-10 w-32 h-32 border border-white/30 rounded-full" />
            <div className="absolute bottom-20 right-5 w-24 h-24 border border-white/20 rounded-full" />
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 h-64 border border-white/10 rounded-full" />
          </div>

          {/* Logo */}
          <div className="relative z-10 mb-6">
            <div className="w-20 h-20 bg-white/20 backdrop-blur-sm rounded-2xl flex items-center justify-center shadow-lg border border-white/30">
              <Play size={40} className="text-white fill-white" />
            </div>
          </div>

          {/* Brand Name */}
          <h2 className="relative z-10 text-white text-2xl font-bold mb-2">MediaHub</h2>
          <p className="relative z-10 text-white/70 text-sm mb-8">Your Media Collection Manager</p>

          {/* Features */}
          <div className="relative z-10 space-y-4 w-full">
            <div className="flex items-center gap-3 bg-white/10 backdrop-blur-sm rounded-lg px-4 py-3 border border-white/10">
              <div className="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
                <Download size={16} className="text-white" />
              </div>
              <span className="text-white/90 text-sm font-medium">Smart Download</span>
            </div>
            <div className="flex items-center gap-3 bg-white/10 backdrop-blur-sm rounded-lg px-4 py-3 border border-white/10">
              <div className="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
                <Zap size={16} className="text-white" />
              </div>
              <span className="text-white/90 text-sm font-medium">Fast Parsing</span>
            </div>
            <div className="flex items-center gap-3 bg-white/10 backdrop-blur-sm rounded-lg px-4 py-3 border border-white/10">
              <div className="w-8 h-8 bg-white/20 rounded-lg flex items-center justify-center">
                <Shield size={16} className="text-white" />
              </div>
              <span className="text-white/90 text-sm font-medium">Secure Storage</span>
            </div>
          </div>
        </div>

        {/* Right Side: Form */}
        <div className="flex-1 p-8 md:p-12 bg-white flex flex-col justify-center">

           {/* Header: Login/Register Toggle (shared for both phone and email) */}
           <div className="flex items-center justify-center gap-8 mb-8 border-b border-zinc-100 min-h-[40px]">
              <button
                onClick={() => { setAuthMode('login'); setError(null); }}
                className={`pb-3 text-sm font-bold transition-all relative ${
                  authMode === 'login' ? 'text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'
                }`}
              >
                {t('auth.login')}
                {authMode === 'login' && (
                  <div className="absolute bottom-0 left-0 w-full h-0.5 bg-red-500 rounded-full" />
                )}
              </button>
              <button
                onClick={() => { setAuthMode('register'); setError(null); }}
                className={`pb-3 text-sm font-bold transition-all relative ${
                  authMode === 'register' ? 'text-zinc-900' : 'text-zinc-400 hover:text-zinc-600'
                }`}
              >
                {t('auth.register')}
                {authMode === 'register' && (
                  <div className="absolute bottom-0 left-0 w-full h-0.5 bg-red-500 rounded-full" />
                )}
              </button>
           </div>

           <div className="space-y-4">

              {/* Error Message */}
              {error && (
                <div className="flex items-center gap-2 p-3 bg-red-50 border border-red-200 rounded-md text-red-600 text-sm">
                  <AlertCircle size={16} />
                  <span>{error}</span>
                </div>
              )}

              {/* --- PHONE MODE INPUTS --- */}
              {loginMode === 'phone' && (
                <>
                  <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11">
                    <div className="px-3 bg-zinc-50 border-r border-zinc-200 flex items-center text-sm text-zinc-500 font-medium w-16 justify-center">
                        +86
                    </div>
                    <input
                      type="tel"
                      placeholder="Mobile Number"
                      value={phone}
                      onChange={(e) => setPhone(e.target.value)}
                      className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                      style={inputStyle}
                    />
                  </div>

                  <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11 relative">
                    <input
                      type={showPassword ? "text" : "password"}
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                      style={inputStyle}
                    />
                    <button
                      onClick={() => setShowPassword(!showPassword)}
                      className="px-3 text-zinc-400 hover:text-zinc-600"
                    >
                      {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>

                  {/* Confirm Password - only shown in register mode */}
                  {authMode === 'register' && (
                    <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11 relative">
                      <input
                        type={showPassword ? "text" : "password"}
                        placeholder="Confirm Password"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                        style={inputStyle}
                      />
                    </div>
                  )}
                </>
              )}

              {/* --- EMAIL MODE INPUTS --- */}
              {loginMode === 'email' && (
                <>

                   <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11">
                     <input
                       type="email"
                       placeholder={t('auth.email')}
                       value={email}
                       onChange={(e) => setEmail(e.target.value)}
                       className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                       style={inputStyle}
                     />
                   </div>
                   <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11 relative">
                     <input
                       type={showPassword ? "text" : "password"}
                       placeholder={t('auth.password')}
                       value={password}
                       onChange={(e) => setPassword(e.target.value)}
                       className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                       style={inputStyle}
                     />
                     <button
                        onClick={() => setShowPassword(!showPassword)}
                        className="px-3 text-zinc-400 hover:text-zinc-600"
                      >
                        {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                     </button>
                   </div>

                   {/* Confirm Password - only shown in register mode */}
                   {authMode === 'register' && (
                     <div className="flex bg-white border border-zinc-200 rounded-md overflow-hidden focus-within:border-zinc-400 transition-colors h-11 relative">
                       <input
                         type={showPassword ? "text" : "password"}
                         placeholder={t('auth.confirmPassword')}
                         value={confirmPassword}
                         onChange={(e) => setConfirmPassword(e.target.value)}
                         className="flex-1 px-3 outline-none text-zinc-900 bg-white text-sm placeholder-zinc-400"
                         style={inputStyle}
                       />
                     </div>
                   )}
                </>
              )}

              {/* Login/Register Button */}
              <button
                onClick={authMode === 'register' ? handleRegister : handleLogin}
                disabled={loading}
                className="w-full bg-[#E53E3E] hover:bg-[#C53030] disabled:opacity-60 disabled:cursor-not-allowed text-white font-bold py-2.5 rounded-md transition-all shadow-md shadow-red-500/20 active:scale-[0.99] mt-2"
              >
                {loading ? 'Loading...' : (authMode === 'register' ? t('auth.register') : t('auth.login'))}
              </button>

              {/* Toggle between Login and Register */}
              <p className="text-center text-sm text-zinc-500 mt-2">
                  {authMode === 'login' ? (
                    <button
                      onClick={() => { setAuthMode('register'); setError(null); }}
                      className="text-indigo-500 hover:underline"
                    >
                      {t('auth.noAccount')}
                    </button>
                  ) : (
                    <button
                      onClick={() => { setAuthMode('login'); setError(null); }}
                      className="text-indigo-500 hover:underline"
                    >
                      {t('auth.hasAccount')}
                    </button>
                  )}
              </p>

              {/* Social Login */}
              <div className="flex justify-center gap-6 mt-8 pt-6 border-t border-zinc-100">
                 {/* 1. Google */}
                 <button className="p-2 rounded-full bg-zinc-50 hover:bg-zinc-100 transition-colors border border-zinc-100" title="Google Quick Login">
                    <GoogleIcon />
                 </button>

                 {/* 2. Douyin (was QQ) */}
                 <button className="p-2 rounded-full bg-zinc-50 text-zinc-400 hover:bg-zinc-100 hover:text-black transition-colors border border-zinc-100" title="Douyin Login">
                    <DouyinIcon />
                 </button>

                 {/* 3. WeChat */}
                 <button className="p-2 rounded-full bg-zinc-50 hover:bg-zinc-100 transition-colors border border-zinc-100" title="WeChat Login">
                    <WeChatIcon />
                 </button>

                 {/* 4. Switcher: Phone <-> Email */}
                 <button
                    onClick={() => setLoginMode(loginMode === 'phone' ? 'email' : 'phone')}
                    className="p-2 rounded-full bg-zinc-50 text-zinc-400 hover:bg-zinc-100 hover:text-[#E53E3E] transition-colors border border-zinc-100"
                    title={loginMode === 'phone' ? "Switch to Email Login" : "Switch to Mobile Login"}
                 >
                    {loginMode === 'phone' ? <Mail size={20} /> : <Smartphone size={20} />}
                 </button>
              </div>

              {/* Terms */}
              <p className="text-center text-xs text-zinc-400 mt-4">
                 By logging in, you agree to our <a href="#" className="text-indigo-500 hover:underline">Terms of Service</a>.
              </p>
           </div>
        </div>
      </div>

      {/* Config Warning */}
      {!isSupabaseConfigured() && (
        <div className="absolute bottom-8 left-1/2 -translate-x-1/2 bg-yellow-500/10 border border-yellow-500/20 text-yellow-200 px-4 py-2 rounded-full flex items-center gap-2 text-xs backdrop-blur-sm">
          <AlertCircle size={14} />
          <span>Demo Mode: Database not configured</span>
        </div>
      )}
    </div>
  );
};
