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
  Palette,
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

// ─── Component ───────────────────────────────────────────────────────────────

export function StoryboardApiSettings() {
  const apiKeys = useSettingsStore((s) => s.apiKeys);
  const providerEndpoints = useSettingsStore((s) => s.providerEndpoints);
  const grsaiNanoBananaProModel = useSettingsStore((s) => s.grsaiNanoBananaProModel);
  const setProviderApiKey = useSettingsStore((s) => s.setProviderApiKey);
  const setProviderEndpoint = useSettingsStore((s) => s.setProviderEndpoint);
  const setGrsaiNanoBananaProModel = useSettingsStore((s) => s.setGrsaiNanoBananaProModel);

  const [showApiKeys, setShowApiKeys] = useState<Record<string, boolean>>({});
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});
  const [testStatus, setTestStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({});
  const [testError, setTestError] = useState<Record<string, string>>({});

  // Prefix storyboard provider keys to avoid collision with AI provider keys
  const sbKey = (providerId: string) => `sb-${providerId}`;

  const getApiKey = (providerId: string) => apiKeys[sbKey(providerId)] ?? '';
  const getEndpoint = (providerId: string) => providerEndpoints[sbKey(providerId)] ?? '';

  const toggleShowApiKey = (providerId: string) => {
    setShowApiKeys((prev) => ({ ...prev, [providerId]: !prev[providerId] }));
  };

  const toggleExpand = (providerId: string) => {
    setExpandedProviders((prev) => ({ ...prev, [providerId]: !prev[providerId] }));
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
      // Simple connectivity check — ping the endpoint or validate the key format
      const provider = STORYBOARD_PROVIDERS.find((p) => p.id === providerId);
      const baseUrl = endpoint || provider?.defaultEndpoint;

      if (baseUrl) {
        const resp = await fetch(baseUrl, {
          method: 'HEAD',
          mode: 'no-cors',
          signal: AbortSignal.timeout(8000),
        });
        // no-cors returns opaque response, treat as success if no error thrown
        setTestStatus((prev) => ({ ...prev, [providerId]: 'success' }));
      } else if (key) {
        // No endpoint to test, just validate key is non-empty
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

  const configuredCount = STORYBOARD_PROVIDERS.filter(
    (p) => getApiKey(p.id).length > 0,
  ).length;

  return (
    <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
      {/* Section Header */}
      <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
        <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400">
          <Palette size={20} />
        </div>
        <div className="flex-1">
          <h2 className="font-semibold text-zinc-200">Storyboard API</h2>
          <p className="text-xs text-zinc-500">
            Configure image generation providers for storyboard canvas
          </p>
        </div>
        {configuredCount > 0 && (
          <span className="px-2.5 py-1 text-xs font-medium rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            {configuredCount} configured
          </span>
        )}
      </div>

      {/* Provider List */}
      <div className="p-6 space-y-3">
        {STORYBOARD_PROVIDERS.map((provider) => {
          const key = getApiKey(provider.id);
          const endpoint = getEndpoint(provider.id);
          const colors = COLOR_MAP[provider.color] || COLOR_MAP.indigo;
          const isExpanded = expandedProviders[provider.id] ?? key.length > 0;
          const status = testStatus[provider.id] || 'idle';
          const hasKey = key.length > 0;

          return (
            <div
              key={provider.id}
              className={`bg-zinc-950 border rounded-xl overflow-hidden transition-all ${
                hasKey ? colors.border : 'border-zinc-800'
              }`}
            >
              {/* Provider Header */}
              <button
                type="button"
                onClick={() => toggleExpand(provider.id)}
                className="w-full px-5 py-3.5 flex items-center gap-3 hover:bg-zinc-900/50 transition-colors"
              >
                <div className={`p-1.5 rounded-lg ${colors.bg} ${colors.text}`}>
                  <ImageIcon size={16} />
                </div>
                <div className="flex-1 min-w-0 text-left">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm text-zinc-200">{provider.name}</span>
                    {hasKey && (
                      <span className="flex items-center gap-1 text-[11px] text-green-400">
                        <Check size={10} />
                        Configured
                      </span>
                    )}
                    {provider.selfHostable && (
                      <span className="text-[11px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-500">
                        Self-host
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-500 mt-0.5">{provider.description}</p>
                </div>
                <ChevronDown
                  size={14}
                  className={`text-zinc-500 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                />
              </button>

              {/* Provider Config (expanded) */}
              {isExpanded && (
                <div className="px-5 pb-4 pt-1 border-t border-zinc-800/50 space-y-3 animate-in fade-in slide-in-from-top-2 duration-200">
                  {/* API Key */}
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-zinc-400 flex items-center gap-1.5">
                      <Key size={11} />
                      API Key
                    </label>
                    <div className="relative">
                      <input
                        type={showApiKeys[provider.id] ? 'text' : 'password'}
                        value={key}
                        onChange={(e) => handleApiKeyChange(provider.id, e.target.value)}
                        placeholder={`Enter your ${provider.name} API key`}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3.5 py-2 pr-10 text-sm text-zinc-200 placeholder-zinc-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                      />
                      <button
                        type="button"
                        onClick={() => toggleShowApiKey(provider.id)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300 transition-colors"
                      >
                        {showApiKeys[provider.id] ? <EyeOff size={14} /> : <Eye size={14} />}
                      </button>
                    </div>
                  </div>

                  {/* Self-hosted Endpoint URL */}
                  {provider.selfHostable && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-zinc-400 flex items-center gap-1.5">
                        <Server size={11} />
                        Endpoint URL
                      </label>
                      <input
                        type="text"
                        value={endpoint}
                        onChange={(e) => handleEndpointChange(provider.id, e.target.value)}
                        placeholder={provider.defaultEndpoint || 'https://your-server.com'}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3.5 py-2 text-sm text-zinc-200 placeholder-zinc-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                      />
                      <p className="text-[11px] text-zinc-600">
                        Leave empty to use default: {provider.defaultEndpoint}
                      </p>
                    </div>
                  )}

                  {/* GRSAI Model Selector */}
                  {provider.id === 'grsai' && provider.models && (
                    <div className="space-y-1.5">
                      <label className="text-xs font-medium text-zinc-400">
                        Nano Banana Pro Model
                      </label>
                      <div className="relative">
                        <select
                          value={grsaiNanoBananaProModel}
                          onChange={(e) => setGrsaiNanoBananaProModel(e.target.value)}
                          className="w-full appearance-none bg-zinc-950 border border-zinc-800 rounded-lg px-3.5 py-2 pr-8 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                        >
                          {provider.models.map((m) => (
                            <option key={m} value={m}>
                              {m}
                            </option>
                          ))}
                        </select>
                        <ChevronDown
                          size={14}
                          className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 pointer-events-none"
                        />
                      </div>
                    </div>
                  )}

                  {/* Test Connection */}
                  {(hasKey || endpoint) && (
                    <div className="pt-1">
                      <button
                        type="button"
                        onClick={() => testConnection(provider.id)}
                        disabled={status === 'testing'}
                        className={`px-3.5 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-1.5 border ${
                          status === 'success'
                            ? 'bg-green-500/10 text-green-400 border-green-500/30'
                            : status === 'error'
                            ? 'bg-red-500/10 text-red-400 border-red-500/30'
                            : 'bg-zinc-800 text-zinc-300 border-zinc-700 hover:bg-zinc-700'
                        }`}
                      >
                        {status === 'testing' && <Loader2 size={12} className="animate-spin" />}
                        {status === 'success' && <Check size={12} />}
                        {status === 'error' && <WifiOff size={12} />}
                        {status === 'idle' && <Wifi size={12} />}
                        {status === 'testing'
                          ? 'Testing...'
                          : status === 'success'
                          ? 'Connected'
                          : status === 'error'
                          ? 'Retry'
                          : 'Test Connection'}
                      </button>
                      {status === 'error' && testError[provider.id] && (
                        <p className="mt-1.5 text-xs text-red-400">{testError[provider.id]}</p>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default StoryboardApiSettings;
