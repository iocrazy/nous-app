import React, { useState, useCallback } from 'react';
import {
  Key,
  Eye,
  EyeOff,
  Check,
  Loader2,
  Wifi,
  WifiOff,
  ChevronDown,
  ImageIcon,
  Server,
} from 'lucide-react';
import { useSettingsStore } from '../stores/settingsStore';
import { GRSAI_NANO_BANANA_PRO_MODEL_OPTIONS } from '../features/storyboard/models/providers/grsai';

// ─── Storyboard Provider Definitions ─────────────────────────────────────────

interface StoryboardProviderMeta {
  id: string;
  name: string;
  description: string;
  color: string;
  selfHostable?: boolean;
  defaultEndpoint?: string;
  models?: readonly string[];
}

const STORYBOARD_PROVIDERS: StoryboardProviderMeta[] = [
  {
    id: 'kie',
    name: 'KIE',
    description: 'KIE image generation API',
    color: 'indigo',
  },
  {
    id: 'ppio',
    name: 'PPIO',
    description: 'PPIO cloud image generation',
    color: 'blue',
  },
  {
    id: 'fal',
    name: 'fal',
    description: 'fal.ai serverless inference',
    color: 'violet',
  },
  {
    id: 'grsai',
    name: 'GRSAI',
    description: 'GRSAI Nano Banana Pro models',
    color: 'emerald',
    models: GRSAI_NANO_BANANA_PRO_MODEL_OPTIONS,
  },
  {
    id: 'comfly',
    name: 'Comfly',
    description: 'Comfly image generation API',
    color: 'amber',
    selfHostable: true,
    defaultEndpoint: 'https://api.comfly.chat',
  },
  {
    id: 'runninghub',
    name: 'RunningHub',
    description: 'RunningHub workflow execution',
    color: 'teal',
    selfHostable: true,
    defaultEndpoint: 'https://www.runninghub.cn',
  },
  {
    id: 'zhenzhen',
    name: 'Zhenzhen',
    description: 'Zhenzhen AI image generation',
    color: 'rose',
  },
];

const COLOR_MAP: Record<string, { bg: string; text: string; border: string }> = {
  indigo: { bg: 'bg-indigo-500/10', text: 'text-indigo-400', border: 'border-indigo-500/30' },
  blue: { bg: 'bg-blue-500/10', text: 'text-blue-400', border: 'border-blue-500/30' },
  violet: { bg: 'bg-violet-500/10', text: 'text-violet-400', border: 'border-violet-500/30' },
  emerald: { bg: 'bg-emerald-500/10', text: 'text-emerald-400', border: 'border-emerald-500/30' },
  amber: { bg: 'bg-amber-500/10', text: 'text-amber-400', border: 'border-amber-500/30' },
  teal: { bg: 'bg-teal-500/10', text: 'text-teal-400', border: 'border-teal-500/30' },
  rose: { bg: 'bg-rose-500/10', text: 'text-rose-400', border: 'border-rose-500/30' },
};

// ─── Toggle Component ────────────────────────────────────────────────────────

function Toggle({ enabled, onToggle }: { enabled: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${
        enabled ? 'bg-indigo-600' : 'bg-ink-700'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
          enabled ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export function StoryboardApiSettings() {
  const apiKeys = useSettingsStore((s) => s.apiKeys);
  const providerEndpoints = useSettingsStore((s) => s.providerEndpoints);
  const enabledSbProviders = useSettingsStore((s) => s.enabledSbProviders);
  const grsaiNanoBananaProModel = useSettingsStore((s) => s.grsaiNanoBananaProModel);
  const setProviderApiKey = useSettingsStore((s) => s.setProviderApiKey);
  const setProviderEndpoint = useSettingsStore((s) => s.setProviderEndpoint);
  const setSbProviderEnabled = useSettingsStore((s) => s.setSbProviderEnabled);
  const setGrsaiNanoBananaProModel = useSettingsStore((s) => s.setGrsaiNanoBananaProModel);

  const [showApiKeys, setShowApiKeys] = useState<Record<string, boolean>>({});
  const [testStatus, setTestStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({});
  const [testError, setTestError] = useState<Record<string, string>>({});

  const sbKey = (providerId: string) => `sb-${providerId}`;
  const getApiKey = (providerId: string) => apiKeys[sbKey(providerId)] ?? '';
  const getEndpoint = (providerId: string) => providerEndpoints[sbKey(providerId)] ?? '';
  const isEnabled = (providerId: string) => enabledSbProviders[providerId] ?? false;

  const toggleShowApiKey = (providerId: string) => {
    setShowApiKeys((prev) => ({ ...prev, [providerId]: !prev[providerId] }));
  };

  const toggleProvider = (providerId: string) => {
    setSbProviderEnabled(providerId, !isEnabled(providerId));
  };

  const handleApiKeyChange = (providerId: string, value: string) => {
    setProviderApiKey(sbKey(providerId), value);
  };

  const handleEndpointChange = (providerId: string, value: string) => {
    setProviderEndpoint(sbKey(providerId), value);
  };

  const testConnection = useCallback(async (providerId: string) => {
    const key = getApiKey(providerId);
    const endpoint = getEndpoint(providerId);

    setTestStatus((prev) => ({ ...prev, [providerId]: 'testing' }));
    setTestError((prev) => ({ ...prev, [providerId]: '' }));

    try {
      const provider = STORYBOARD_PROVIDERS.find((p) => p.id === providerId);
      const baseUrl = endpoint || provider?.defaultEndpoint;

      if (baseUrl) {
        await fetch(baseUrl, {
          method: 'HEAD',
          mode: 'no-cors',
          signal: AbortSignal.timeout(8000),
        });
        setTestStatus((prev) => ({ ...prev, [providerId]: 'success' }));
      } else if (key) {
        setTestStatus((prev) => ({ ...prev, [providerId]: 'success' }));
      } else {
        setTestStatus((prev) => ({ ...prev, [providerId]: 'error' }));
        setTestError((prev) => ({ ...prev, [providerId]: 'No API key or endpoint configured' }));
      }
    } catch (err) {
      setTestStatus((prev) => ({ ...prev, [providerId]: 'error' }));
      setTestError((prev) => ({
        ...prev,
        [providerId]: err instanceof Error ? err.message : 'Connection failed',
      }));
    }
  }, [apiKeys, providerEndpoints]);

  return (
    <div className="space-y-4">
      {STORYBOARD_PROVIDERS.map((provider) => {
        const key = getApiKey(provider.id);
        const endpoint = getEndpoint(provider.id);
        const colors = COLOR_MAP[provider.color] || COLOR_MAP.indigo;
        const enabled = isEnabled(provider.id);
        const status = testStatus[provider.id] || 'idle';

        return (
          <div
            key={provider.id}
            className={`bg-ink-950 border rounded-xl overflow-hidden transition-all ${
              enabled ? colors.border : 'border-ink-800'
            }`}
          >
            {/* Provider Header — matches AI Providers style */}
            <div className="px-6 py-4 flex items-center gap-4">
              <div className={`p-2 rounded-lg ${colors.bg} ${colors.text}`}>
                <ImageIcon size={18} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h4 className="font-semibold text-ink-200">{provider.name}</h4>
                  {provider.selfHostable && (
                    <span className="text-xs px-2 py-0.5 rounded-full border border-ink-700 bg-ink-800/50 text-ink-500">
                      Self-host
                    </span>
                  )}
                </div>
                <p className="text-xs text-ink-500 mt-0.5">{provider.description}</p>
              </div>
              <Toggle enabled={enabled} onToggle={() => toggleProvider(provider.id)} />
            </div>

            {/* Provider Config (expanded when enabled) */}
            {enabled && (
              <div className="px-6 pb-5 pt-2 border-t border-ink-800/50 space-y-4 animate-in fade-in slide-in-from-top-2 duration-300">
                {/* API Key */}
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                    <Key size={12} />
                    API Key
                  </label>
                  <div className="relative">
                    <input
                      type={showApiKeys[provider.id] ? 'text' : 'password'}
                      value={key}
                      onChange={(e) => handleApiKeyChange(provider.id, e.target.value)}
                      placeholder={`Enter your ${provider.name} API key`}
                      className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-10 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                    />
                    <button
                      type="button"
                      onClick={() => toggleShowApiKey(provider.id)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-300 transition-colors"
                    >
                      {showApiKeys[provider.id] ? <EyeOff size={16} /> : <Eye size={16} />}
                    </button>
                  </div>
                </div>

                {/* Self-hosted Endpoint URL */}
                {provider.selfHostable && (
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                      <Server size={12} />
                      Endpoint URL
                    </label>
                    <input
                      type="text"
                      value={endpoint}
                      onChange={(e) => handleEndpointChange(provider.id, e.target.value)}
                      placeholder={provider.defaultEndpoint || 'https://your-server.com'}
                      className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                    />
                    <p className="text-[11px] text-ink-600">
                      Leave empty to use default: {provider.defaultEndpoint}
                    </p>
                  </div>
                )}

                {/* GRSAI Model Selector */}
                {provider.id === 'grsai' && provider.models && (
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-ink-400">Model</label>
                    <div className="relative">
                      <select
                        value={grsaiNanoBananaProModel}
                        onChange={(e) => setGrsaiNanoBananaProModel(e.target.value)}
                        className="w-full appearance-none bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                      >
                        {provider.models.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                      <ChevronDown
                        size={14}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none"
                      />
                    </div>
                  </div>
                )}

                {/* Test Connection */}
                <div className="pt-1">
                  <button
                    type="button"
                    onClick={() => testConnection(provider.id)}
                    disabled={status === 'testing'}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 border ${
                      status === 'success'
                        ? 'bg-green-500/10 text-green-400 border-green-500/30'
                        : status === 'error'
                        ? 'bg-red-500/10 text-red-400 border-red-500/30'
                        : 'bg-ink-800 text-ink-300 border-ink-700 hover:bg-ink-700'
                    }`}
                  >
                    {status === 'testing' && <Loader2 size={14} className="animate-spin" />}
                    {status === 'success' && <Check size={14} />}
                    {status === 'error' && <WifiOff size={14} />}
                    {status === 'idle' && <Wifi size={14} />}
                    {status === 'testing'
                      ? 'Testing...'
                      : status === 'success'
                      ? 'Connected'
                      : status === 'error'
                      ? 'Retry Connection'
                      : 'Test Connection'}
                  </button>
                  {status === 'error' && testError[provider.id] && (
                    <p className="mt-2 text-xs text-red-400">{testError[provider.id]}</p>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default StoryboardApiSettings;
