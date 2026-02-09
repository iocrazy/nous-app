// components/ParseModeCard.tsx

import React, { useState, useEffect } from 'react';
import { Zap, Globe, RefreshCw } from 'lucide-react';
import { getAuthHeaders } from '../services/parserService';

interface ParseModeCardProps {
  onModeChange?: (mode: string) => void;
}

const API_BASE = 'VITE_API_URL' in import.meta.env ? (import.meta.env.VITE_API_URL || '') : 'http://localhost:8080';

export const ParseModeCard: React.FC<ParseModeCardProps> = ({ onModeChange }) => {
  const [mode, setMode] = useState<'lighthttp' | 'drissionpage'>('lighthttp');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchParseMode();
  }, []);

  const fetchParseMode = async () => {
    try {
      const response = await fetch(`${API_BASE}/api/v1/settings/parse-mode`, {
        method: 'GET',
        headers: getAuthHeaders(),
      });

      if (response.ok) {
        const data = await response.json();
        setMode(data.mode);
      }
    } catch (err) {
      console.error('Failed to fetch parse mode:', err);
    }
  };

  const toggleMode = async () => {
    const newMode = mode === 'lighthttp' ? 'drissionpage' : 'lighthttp';

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`${API_BASE}/api/v1/settings/parse-mode`, {
        method: 'PUT',
        headers: {
          ...getAuthHeaders(),
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ mode: newMode }),
      });

      if (response.ok) {
        setMode(newMode);
        onModeChange?.(newMode);
      } else {
        setError('Failed to update');
      }
    } catch (err) {
      setError('Network error');
      console.error('Failed to set parse mode:', err);
    } finally {
      setLoading(false);
    }
  };

  const isLightHttp = mode === 'lighthttp';

  return (
    <div className="p-3 md:p-5 rounded-xl bg-zinc-900/50 border border-zinc-800/50 hover:border-zinc-700 transition-colors text-center flex flex-col items-center justify-center min-h-0">
      <div className={`w-8 h-8 md:w-10 md:h-10 rounded-lg flex items-center justify-center mb-2 ${
        isLightHttp ? 'bg-yellow-900/30 text-yellow-400' : 'bg-blue-900/30 text-blue-400'
      }`}>
        {isLightHttp ? (
          <>
            <Zap size={16} className="md:hidden" />
            <Zap size={20} className="hidden md:block" />
          </>
        ) : (
          <>
            <Globe size={16} className="md:hidden" />
            <Globe size={20} className="hidden md:block" />
          </>
        )}
      </div>
      <h4 className="font-semibold text-zinc-200 text-xs md:text-base mb-0.5">Parse Mode</h4>
      <button
        onClick={toggleMode}
        disabled={loading}
        className={`text-xs md:text-sm font-mono mb-1 ${
          loading ? 'opacity-50' : 'hover:opacity-80'
        } ${isLightHttp ? 'text-yellow-500' : 'text-blue-500'}`}
      >
        {loading ? (
          <RefreshCw size={14} className="animate-spin inline" />
        ) : (
          isLightHttp ? 'LightHTTP' : 'DrissionPage'
        )}
      </button>
      <p className="text-[10px] md:text-xs text-zinc-500">
        {isLightHttp ? 'Fast HTTP parsing' : 'Browser-based'}
      </p>
      {error && (
        <p className="text-[10px] text-red-400 mt-0.5">{error}</p>
      )}
    </div>
  );
};

export default ParseModeCard;
