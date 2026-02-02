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
    <div className="bg-zinc-900 border border-zinc-800 rounded-xl p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          {isLightHttp ? (
            <Zap size={18} className="text-yellow-500" />
          ) : (
            <Globe size={18} className="text-blue-500" />
          )}
          <span className="text-sm font-medium text-zinc-300">Parse Mode</span>
        </div>
        <button
          onClick={toggleMode}
          disabled={loading}
          className={`
            relative w-12 h-6 rounded-full transition-colors duration-200
            ${isLightHttp ? 'bg-yellow-500/20' : 'bg-blue-500/20'}
            ${loading ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer hover:opacity-80'}
          `}
        >
          <div
            className={`
              absolute top-1 w-4 h-4 rounded-full transition-all duration-200
              ${isLightHttp
                ? 'left-1 bg-yellow-500'
                : 'left-7 bg-blue-500'
              }
            `}
          >
            {loading && (
              <RefreshCw size={10} className="animate-spin text-white m-0.5" />
            )}
          </div>
        </button>
      </div>

      <div className="space-y-1">
        <div className={`text-lg font-semibold ${isLightHttp ? 'text-yellow-500' : 'text-blue-500'}`}>
          {isLightHttp ? 'LightHTTP' : 'DrissionPage'}
        </div>
        <div className="text-xs text-zinc-500">
          {isLightHttp
            ? 'Fast HTTP parsing'
            : 'Browser-based parsing'
          }
        </div>
      </div>

      {error && (
        <div className="mt-2 text-xs text-red-400">{error}</div>
      )}
    </div>
  );
};

export default ParseModeCard;
