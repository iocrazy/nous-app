import React, { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import {
  Brain,
  Zap,
  Server,
  Globe,
  Key,
  Eye,
  EyeOff,
  Check,
  Loader2,
  Wifi,
  WifiOff,
  Settings,
  Save,
  ChevronDown,
  ToggleLeft,
  ToggleRight,
  Sparkles,
  FileText,
  Search,
  AlertCircle,
  Moon,
  MessageSquare,
  Cloud,
  ImageIcon,
  Mic,
  Plus,
  X,
  Languages,
  ExternalLink,
} from 'lucide-react';
import { AISettings as AISettingsType, AIProviderConfig, NousModelPublic, AILibraryAgent } from '../types';
import { saveAISettings as saveAISettingsApi, testAIConnection as testAIConnectionApi, getNousModels } from '../services/aiService';
import { aiLibraryService } from '../services/aiLibraryService';
import { StoryboardApiSettings } from './StoryboardApiSettings';
import { MCPServersPanel } from './MCPServersPanel';
import { ApprovalsPanel } from './ApprovalsPanel';
import { TokenBillingDashboard } from './TokenBillingDashboard';
import { NousCenterVerifyPanel } from '../features/canvas-core/smart/NousCenterVerifyPanel';
import { useSettingsStore } from '../stores/settingsStore';

interface AISettingsProps {
  settings: AISettingsType;
  onSave: (settings: AISettingsType) => void;
}

// Provider metadata definitions
const PROVIDER_META: Record<
  string,
  {
    name: string;
    description: string;
    icon: React.ReactNode;
    color: string;
    badge?: string;
    isLocal?: boolean;
    defaultBaseUrl?: string;
    models: string[];
    whisperModels?: string[];
    summaryModels?: string[];
    analysisModels?: string[];
    apiKeyLabel?: string;
    appIdField?: boolean;
    // Provider console / API-key page, rendered as an external link in
    // the card header so users can jump straight to where keys live.
    website?: string;
  }
> = {
  openai: {
    name: 'OpenAI',
    description: 'Cloud-hosted GPT & Whisper models',
    icon: <Sparkles size={18} />,
    color: 'emerald',
    badge: 'Recommended',
    website: 'https://platform.openai.com/api-keys',
    models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
    whisperModels: ['whisper-1'],
    summaryModels: ['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'],
    analysisModels: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
  },
  deepseek: {
    name: 'DeepSeek',
    description: 'Cost-effective cloud AI',
    website: 'https://platform.deepseek.com/api_keys',
    icon: <Zap size={18} />,
    color: 'blue',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    summaryModels: ['deepseek-chat', 'deepseek-reasoner'],
  },
  doubao: {
    name: 'Doubao',
    description: 'ByteDance Ark — 豆包大模型',
    icon: <Globe size={18} />,
    color: 'violet',
    defaultBaseUrl: 'https://ark.cn-beijing.volces.com/api/v3',
    website: 'https://console.volcengine.com/ark',
    models: ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-lite-260215', 'doubao-pro', 'doubao-lite', 'doubao-pro-32k'],
    summaryModels: ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-lite-260215', 'doubao-pro', 'doubao-lite'],
    analysisModels: ['doubao-seed-2-0-pro-260215', 'doubao-seed-2-0-lite-260215'],
  },
  minimax: {
    name: 'MiniMax',
    description: 'MiniMax cloud AI',
    website: 'https://platform.minimaxi.com/user-center/basic-information/interface-key',
    icon: <MessageSquare size={18} />,
    color: 'amber',
    models: ['MiniMax-M2.5', 'MiniMax-M2.5-highspeed', 'MiniMax-M2.1', 'MiniMax-M2'],
    summaryModels: ['MiniMax-M2.5', 'MiniMax-M2.5-highspeed', 'MiniMax-M2.1'],
  },
  kimi: {
    name: 'Kimi',
    description: 'Moonshot AI',
    website: 'https://platform.moonshot.cn/console/api-keys',
    icon: <Moon size={18} />,
    color: 'teal',
    models: ['kimi-k2.5', 'kimi-k2', 'moonshot-v1-128k', 'moonshot-v1-32k', 'moonshot-v1-8k'],
    summaryModels: ['kimi-k2.5', 'kimi-k2', 'moonshot-v1-32k'],
    analysisModels: ['kimi-k2.5'],
  },
  qwen: {
    name: 'Qwen (Bailian)',
    description: 'Alibaba Cloud Bailian — Qwen models',
    icon: <Cloud size={18} />,
    color: 'rose',
    defaultBaseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    website: 'https://bailian.console.aliyun.com/?tab=model#/api-key',
    models: [
      'qwen3.5-plus', 'qwen3.5-flash', 'qwen3-max',
      'qwen-plus', 'qwen-flash', 'qwen-turbo',
      'qwen-coder-plus', 'qwq-plus',
    ],
    summaryModels: ['qwen3.5-plus', 'qwen3-max', 'qwen-plus', 'qwen-turbo'],
    analysisModels: ['qwen3.5-plus', 'qwen3-vl-plus', 'qwen-vl-max'],
  },
  modelscope: {
    name: 'ModelScope',
    description: 'ModelScope (魔搭) — community inference, free tier',
    icon: <Brain size={18} />,
    color: 'violet',
    badge: 'Free Tier',
    website: 'https://modelscope.cn/my/myaccesstoken',
    defaultBaseUrl: 'https://api-inference.modelscope.cn/v1',
    models: [
      'Qwen/Qwen3-235B-A22B',
      'Qwen/Qwen2.5-72B-Instruct',
      'deepseek-ai/DeepSeek-V3.1',
      'deepseek-ai/DeepSeek-R1',
      'ZhipuAI/GLM-4.6',
    ],
    summaryModels: ['Qwen/Qwen2.5-72B-Instruct', 'deepseek-ai/DeepSeek-V3.1'],
  },
  volcengine: {
    name: 'Volcengine',
    description: 'ByteDance — 火山引擎语音识别',
    icon: <Mic size={18} />,
    color: 'cyan',
    whisperModels: ['bigasr', 'seed-asr'],
    models: [],
    apiKeyLabel: 'Access Token / API Key',
    appIdField: true,
    website: 'https://console.volcengine.com/speech/app',
  },
  ollama: {
    name: 'Ollama',
    description: 'Local models, free & private',
    icon: <Server size={18} />,
    color: 'orange',
    isLocal: true,
    defaultBaseUrl: 'http://localhost:11434',
    website: 'https://ollama.com/download',
    models: ['qwen2.5:7b', 'qwen2.5:14b', 'llama3.1:8b', 'llama3.1:70b', 'mistral:7b', 'gemma2:9b'],
  },
  lmstudio: {
    name: 'LM Studio',
    description: 'Local models via LM Studio',
    icon: <Server size={18} />,
    color: 'pink',
    isLocal: true,
    defaultBaseUrl: 'http://localhost:1234',
    website: 'https://lmstudio.ai',
    models: [],
  },
};

const LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'Auto Detect' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
];

const COLOR_MAP: Record<string, { bg: string; text: string; border: string; badge: string }> = {
  emerald: {
    bg: 'bg-emerald-500/10',
    text: 'text-emerald-400',
    border: 'border-emerald-500/30',
    badge: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30',
  },
  blue: {
    bg: 'bg-blue-500/10',
    text: 'text-blue-400',
    border: 'border-blue-500/30',
    badge: 'bg-blue-500/20 text-blue-300 border-blue-500/30',
  },
  violet: {
    bg: 'bg-violet-500/10',
    text: 'text-violet-400',
    border: 'border-violet-500/30',
    badge: 'bg-violet-500/20 text-violet-300 border-violet-500/30',
  },
  orange: {
    bg: 'bg-orange-500/10',
    text: 'text-orange-400',
    border: 'border-orange-500/30',
    badge: 'bg-orange-500/20 text-orange-300 border-orange-500/30',
  },
  pink: {
    bg: 'bg-pink-500/10',
    text: 'text-pink-400',
    border: 'border-pink-500/30',
    badge: 'bg-pink-500/20 text-pink-300 border-pink-500/30',
  },
  amber: {
    bg: 'bg-amber-500/10',
    text: 'text-amber-400',
    border: 'border-amber-500/30',
    badge: 'bg-amber-500/20 text-amber-300 border-amber-500/30',
  },
  teal: {
    bg: 'bg-teal-500/10',
    text: 'text-teal-400',
    border: 'border-teal-500/30',
    badge: 'bg-teal-500/20 text-teal-300 border-teal-500/30',
  },
  rose: {
    bg: 'bg-rose-500/10',
    text: 'text-rose-400',
    border: 'border-rose-500/30',
    badge: 'bg-rose-500/20 text-rose-300 border-rose-500/30',
  },
};

type ProviderTab = 'text' | 'image';
type TaskTab = 'media' | 'storyboard';

const IMAGE_PROVIDERS = [
  { id: 'kie', name: 'KIE' },
  { id: 'ppio', name: 'PPIO' },
  { id: 'fal', name: 'fal' },
  { id: 'grsai', name: 'GRSAI' },
  { id: 'comfly', name: 'Comfly' },
  { id: 'runninghub', name: 'RunningHub' },
  { id: 'zhenzhen', name: 'Zhenzhen' },
] as const;

/**
 * Curated-whitelist model picker for a provider. Renders chips for the
 * already-enabled models and an "+ Add Model" affordance that drops down
 * a filterable list of the provider's full catalog (minus what's already
 * enabled). The catalog is whatever Test Connection populated into
 * ``config.models``; if empty we tell the user to test first.
 */
const EnabledModelsField: React.FC<{
  providerKey: string;
  enabledModels: string[];
  catalog: string[];
  onAdd: (modelId: string) => void;
  onRemove: (modelId: string) => void;
}> = ({ providerKey, enabledModels, catalog, onAdd, onRemove }) => {
  const [picking, setPicking] = useState(false);
  const [filter, setFilter] = useState('');

  const remaining = useMemo(
    () =>
      catalog.filter(
        (m) => !enabledModels.includes(m) && m.toLowerCase().includes(filter.toLowerCase()),
      ),
    [catalog, enabledModels, filter],
  );

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-ink-400">Enabled Models</label>
      <div className="flex flex-wrap items-center gap-2">
        {enabledModels.map((m) => (
          <span
            key={m}
            className="inline-flex items-center gap-1.5 rounded-full bg-indigo-500/15 border border-indigo-500/30 px-3 py-1 text-xs font-mono text-indigo-200"
          >
            {m}
            <button
              type="button"
              onClick={() => onRemove(m)}
              className="text-indigo-300 hover:text-white transition-colors"
              aria-label={`Remove ${m}`}
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {!picking && (
          <button
            type="button"
            onClick={() => {
              setPicking(true);
              setFilter('');
            }}
            className="inline-flex items-center gap-1 rounded-full border border-dashed border-ink-700 px-3 py-1 text-xs text-ink-400 hover:text-ink-50 hover:border-ink-500 transition-colors"
          >
            <Plus size={12} />
            Add Model
          </button>
        )}
      </div>

      {picking && (
        <div className="rounded-lg border border-ink-800 bg-ink-950 p-2 space-y-1.5">
          <div className="flex items-center gap-2">
            <input
              autoFocus
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter models..."
              className="flex-1 bg-transparent text-xs text-ink-200 placeholder:text-ink-600 focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setPicking(false)}
              className="text-ink-500 hover:text-ink-200 transition-colors"
              aria-label="Close picker"
            >
              <X size={14} />
            </button>
          </div>
          {catalog.length === 0 ? (
            <div className="text-xs text-ink-500 px-1 py-2">
              Catalog empty — run Test Connection first.
            </div>
          ) : remaining.length === 0 ? (
            <div className="text-xs text-ink-500 px-1 py-2">
              {filter ? 'No matches' : 'All models already enabled'}
            </div>
          ) : (
            <div className="max-h-60 overflow-y-auto space-y-0.5">
              {remaining.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    onAdd(m);
                    setFilter('');
                  }}
                  className="w-full text-left rounded px-2 py-1 text-xs font-mono text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors"
                >
                  {m}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <p className="text-[11px] text-ink-500">
        Only enabled models are shown to agents in the AI Library — provider:{' '}
        <span className="font-mono">{providerKey}</span>
      </p>
    </div>
  );
};

export const AISettings: React.FC<AISettingsProps> = ({ settings, onSave }) => {
  const [providerTab, setProviderTab] = useState<ProviderTab>('text');
  const [taskTab, setTaskTab] = useState<TaskTab>('media');
  const enabledSbProviders = useSettingsStore((s) => s.enabledSbProviders);
  const [localSettings, setLocalSettings] = useState<AISettingsType>(() => ({
    ...settings,
  }));
  const [showApiKeys, setShowApiKeys] = useState<Record<string, boolean>>({});
  const [connectionStatus, setConnectionStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({});
  const [connectionError, setConnectionError] = useState<Record<string, string>>({});
  // Daily-quota counters from Test Connection (ModelScope rate-limit headers).
  const [quotaInfo, setQuotaInfo] = useState<Record<string, Record<string, number> | undefined>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [nousModels, setNousModels] = useState<NousModelPublic[]>([]);
  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const isDirtyRef = useRef(false);

  // AuthContext loads AI settings asynchronously after login, so the prop
  // may hydrate after this form mounted with empty defaults. Re-sync the
  // form whenever the prop changes — unless the user has unsaved edits.
  useEffect(() => {
    if (!isDirtyRef.current) {
      setLocalSettings({ ...settings });
    }
  }, [settings]);

  // All user-driven mutations go through this so a later prop refresh
  // can't silently discard unsaved edits.
  const editLocalSettings = (
    updater: (prev: AISettingsType) => AISettingsType
  ) => {
    isDirtyRef.current = true;
    setLocalSettings(updater);
  };

  useEffect(() => {
    getNousModels().then(setNousModels).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    aiLibraryService
      .listAgents()
      .then((list) => {
        if (!cancelled) setAgents(list);
      })
      .catch((err) => {
        console.error('[AISettings] listAgents failed:', err);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Toggle AI globally
  const toggleAIEnabled = () => {
    editLocalSettings((prev) => ({
      ...prev,
      ai_enabled: !prev.ai_enabled,
    }));
  };

  // Update preferred language
  const setPreferredLanguage = (lang: string) => {
    editLocalSettings((prev) => ({
      ...prev,
      preferred_language: lang,
    }));
  };

  // Toggle provider enabled
  const toggleProvider = (providerKey: string) => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers[providerKey as keyof typeof providers] || { enabled: false };
      const meta = PROVIDER_META[providerKey];
      providers[providerKey as keyof typeof providers] = {
        ...current,
        enabled: !current.enabled,
        base_url: current.base_url || meta?.defaultBaseUrl,
        selected_model: current.selected_model || meta?.models[0],
      };
      return { ...prev, providers };
    });
  };

  // Update provider config field
  const updateProviderField = (providerKey: string, field: keyof AIProviderConfig, value: string | string[] | boolean) => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers[providerKey as keyof typeof providers] || { enabled: false };
      providers[providerKey as keyof typeof providers] = {
        ...current,
        [field]: value,
      };
      return { ...prev, providers };
    });
  };

  // Add a model to the provider's curated whitelist (de-duped).
  const addEnabledModel = (providerKey: string, modelId: string) => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers[providerKey as keyof typeof providers] || { enabled: false };
      const existing = current.enabled_models ?? (current.selected_model ? [current.selected_model] : []);
      if (existing.includes(modelId)) return prev;
      providers[providerKey as keyof typeof providers] = {
        ...current,
        enabled_models: [...existing, modelId],
        // Keep selected_model in sync as the implicit default for legacy
        // task_assignment paths that still expect a single model.
        selected_model: current.selected_model || modelId,
      };
      return { ...prev, providers };
    });
  };

  // Remove a model from the whitelist. If it was the selected_model,
  // promote the first remaining model so legacy paths don't break.
  const removeEnabledModel = (providerKey: string, modelId: string) => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers[providerKey as keyof typeof providers] || { enabled: false };
      const existing = current.enabled_models ?? (current.selected_model ? [current.selected_model] : []);
      const next = existing.filter((m) => m !== modelId);
      providers[providerKey as keyof typeof providers] = {
        ...current,
        enabled_models: next,
        selected_model:
          current.selected_model === modelId ? next[0] ?? '' : current.selected_model,
      };
      return { ...prev, providers };
    });
  };

  // Update task assignment
  const updateTaskAssignment = (task: keyof AISettingsType['task_assignment'], provider: string) => {
    editLocalSettings((prev) => ({
      ...prev,
      task_assignment: {
        ...prev.task_assignment,
        [task]: provider,
      },
    }));
  };

  // Toggle API key visibility
  const toggleShowApiKey = (providerKey: string) => {
    setShowApiKeys((prev) => ({
      ...prev,
      [providerKey]: !prev[providerKey],
    }));
  };

  // Test connection - uses backend API for cloud providers, direct for local
  const testConnection = useCallback(async (providerKey: string) => {
    const provider = localSettings.providers[providerKey as keyof typeof localSettings.providers];
    if (!provider) return;

    const meta = PROVIDER_META[providerKey];
    const isLocal = meta?.isLocal || false;

    setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'testing' }));
    setConnectionError((prev) => ({ ...prev, [providerKey]: '' }));

    try {
      if (isLocal) {
        // Direct connection test for local providers
        if (!provider.base_url) return;
        const url = `${provider.base_url.replace(/\/+$/, '')}/v1/models`;
        const response = await fetch(url, {
          method: 'GET',
          signal: AbortSignal.timeout(10000),
        });

        if (response.ok) {
          const data = await response.json();
          if (data?.data && Array.isArray(data.data)) {
            const modelIds = data.data.map((m: { id: string }) => m.id);
            if (modelIds.length > 0) {
              updateProviderField(providerKey, 'models', modelIds);
              if (!provider.selected_model || !modelIds.includes(provider.selected_model)) {
                updateProviderField(providerKey, 'selected_model', modelIds[0]);
              }
            }
          }
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'success' }));
        } else {
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'error' }));
          setConnectionError((prev) => ({ ...prev, [providerKey]: `Server responded with status ${response.status}` }));
        }
      } else {
        // Use backend API for cloud providers
        const result = await testAIConnectionApi(providerKey, {
          base_url: provider.base_url,
          api_key: provider.api_key,
          app_id: provider.app_id,
        });

        if (result.success) {
          if (result.models && result.models.length > 0) {
            updateProviderField(providerKey, 'models', result.models);
            if (!provider.selected_model || !result.models.includes(provider.selected_model)) {
              updateProviderField(providerKey, 'selected_model', result.models[0]);
            }
          }
          setQuotaInfo((prev) => ({ ...prev, [providerKey]: result.quota || undefined }));
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'success' }));
        } else {
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'error' }));
          setConnectionError((prev) => ({ ...prev, [providerKey]: result.error || 'Connection failed' }));
        }
      }
    } catch (err) {
      setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'error' }));
      setConnectionError((prev) => ({
        ...prev,
        [providerKey]: err instanceof Error ? err.message : 'Connection failed',
      }));
    }
  }, [localSettings.providers]);

  // Save handler - persists to API then updates local state
  const handleSave = async () => {
    setIsSaving(true);
    setSaveSuccess(false);
    setSaveError(null);
    try {
      await saveAISettingsApi(localSettings);
      isDirtyRef.current = false;
      onSave(localSettings);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save settings');
    } finally {
      setIsSaving(false);
    }
  };

  // Helper: get provider config
  const getProviderConfig = (key: string): AIProviderConfig => {
    return localSettings.providers[key as keyof typeof localSettings.providers] || { enabled: false };
  };

  // Helper: get enabled providers for task assignment dropdowns
  const getEnabledProviders = (): { key: string; name: string }[] => {
    return Object.entries(localSettings.providers)
      .filter(([, config]) => config?.enabled)
      .map(([key]) => ({
        key,
        name: PROVIDER_META[key]?.name || key,
      }));
  };

  // Helper: build transcription task options (provider:model format).
  // Transcription doesn't use agent framework — it stays as provider:model.
  const getTranscriptionOptions = (): { value: string; label: string }[] => {
    const options: { value: string; label: string }[] = [];
    const enabledProviders = getEnabledProviders();

    for (const { key, name } of enabledProviders) {
      const meta = PROVIDER_META[key];
      const models = meta?.whisperModels || [];
      for (const model of models) {
        options.push({ value: `${key}:${model}`, label: `${name} ${model}` });
      }
    }

    // Append Nous platform transcription models
    const matchingNousModels = nousModels.filter((m) => m.category === 'transcription');
    for (const model of matchingNousModels) {
      const pricingLabel =
        model.pricing_type === 'per_hour'
          ? `${model.pricing_value} pts/hr`
          : model.pricing_type === 'per_request'
            ? `${model.pricing_value} pts`
            : `${model.pricing_value} pts/1k tokens`;
      options.push({
        value: model.name,
        label: `${model.display_name} (${pricingLabel})`,
      });
    }

    if (options.length === 0) {
      options.push({ value: '', label: 'No Provider Enabled' });
    }

    return options;
  };

  // Helper: build agent picker options, grouped by system preset vs. user's own.
  // Used for summarization / visual_analysis / script_generation — all run
  // through the agent framework and the value is an agent slug.
  type AgentOption = { value: string; label: string; group: 'system' | 'mine' };
  const getAgentOptions = (): AgentOption[] => {
    return agents.map((a) => ({
      value: a.slug,
      label: a.is_system_preset ? `[System] ${a.name}` : `${a.name} (mine)`,
      group: a.is_system_preset ? 'system' : 'mine',
    }));
  };

  // Render an agent <select> with optgroup (system vs mine) + legacy value fallback.
  const renderAgentSelect = (
    taskKey: keyof AISettingsType['task_assignment'],
    currentValue: string,
  ) => {
    const options = getAgentOptions();
    const systemOptions = options.filter((o) => o.group === 'system');
    const mineOptions = options.filter((o) => o.group === 'mine');
    const knownSlugs = new Set(options.map((o) => o.value));
    const isLegacy = currentValue !== '' && !knownSlugs.has(currentValue);

    return (
      <div className="relative">
        <select
          value={currentValue}
          onChange={(e) => updateTaskAssignment(taskKey, e.target.value)}
          className="appearance-none bg-ink-800 border border-ink-700 rounded-lg px-4 py-2 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer min-w-[220px]"
        >
          {options.length === 0 && (
            <option value="">No Agents Available</option>
          )}
          {isLegacy && (
            <option value={currentValue}>{currentValue} (legacy — please reselect)</option>
          )}
          {systemOptions.length > 0 && (
            <optgroup label="System">
              {systemOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </optgroup>
          )}
          {mineOptions.length > 0 && (
            <optgroup label="My Agents">
              {mineOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </optgroup>
          )}
        </select>
        <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
      </div>
    );
  };

  // Render toggle switch
  const renderToggle = (enabled: boolean, onToggle: () => void, disabled = false) => (
    <button
      onClick={onToggle}
      disabled={disabled}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
        disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
      } ${enabled ? 'bg-indigo-600' : 'bg-ink-700'}`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
          enabled ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Master AI Toggle Section */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50 flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400">
            <Brain size={20} />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-ink-200">AI Intelligence</h2>
            <p className="text-xs text-ink-500">Configure AI providers for transcription, summarization, and analysis</p>
          </div>
        </div>

        <div className="p-6 space-y-5">
          {/* AI Enabled Toggle */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`w-2.5 h-2.5 rounded-full ${localSettings.ai_enabled ? 'bg-green-400 shadow-lg shadow-green-400/30' : 'bg-ink-600'}`} />
              <span className="font-medium text-ink-200">AI Enabled</span>
            </div>
            <button
              onClick={toggleAIEnabled}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 ${
                localSettings.ai_enabled
                  ? 'bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20'
                  : 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/30 hover:bg-indigo-500/20'
              }`}
            >
              {localSettings.ai_enabled ? (
                <>
                  <ToggleRight size={16} />
                  Disable AI
                </>
              ) : (
                <>
                  <ToggleLeft size={16} />
                  Enable AI
                </>
              )}
            </button>
          </div>

          {/* Per-resource AI tasks now run via the intent tags (Transcript /
              Summary / Analyze) attached on the parse page or MediaCard.
              Replaces the old global Auto-Transcribe / Auto-Summarize toggles. */}
          <div className={`space-y-4 transition-opacity ${localSettings.ai_enabled ? 'opacity-100' : 'opacity-40 pointer-events-none'}`}>
            <div className="flex items-center justify-between py-2">
              <span className="text-sm text-ink-300">Preferred Language</span>
              <div className="relative">
                <select
                  value={localSettings.preferred_language}
                  onChange={(e) => setPreferredLanguage(e.target.value)}
                  disabled={!localSettings.ai_enabled}
                  className="appearance-none bg-ink-800 border border-ink-700 rounded-lg px-4 py-2 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                >
                  {LANGUAGE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
                <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Task Assignment Section */}
      <section className={`bg-ink-900 border border-ink-800 rounded-xl overflow-hidden transition-opacity ${
        localSettings.ai_enabled ? 'opacity-100' : 'opacity-40 pointer-events-none'
      }`}>
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50 flex items-center gap-3">
          <div className="p-2 bg-purple-500/10 rounded-lg text-purple-400">
            <Zap size={20} />
          </div>
          <div>
            <h2 className="font-semibold text-ink-200">Task Assignment</h2>
            <p className="text-xs text-ink-500">Choose which provider handles each AI task</p>
          </div>
        </div>

        {/* Task Tab Switcher */}
        <div className="px-6 pt-4 pb-2">
          <div className="flex gap-1 p-1 bg-ink-950 rounded-lg">
            <button
              type="button"
              onClick={() => setTaskTab('media')}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                taskTab === 'media'
                  ? 'bg-ink-800 text-ink-100 shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              Media
            </button>
            <button
              type="button"
              onClick={() => setTaskTab('storyboard')}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                taskTab === 'storyboard'
                  ? 'bg-ink-800 text-ink-100 shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              Storyboard
            </button>
          </div>
        </div>

        {/* Media Tasks */}
        {taskTab === 'media' && (
        <div className="px-6 pb-6 pt-2 space-y-5">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Transcription</span>
            </div>
            <div className="relative">
              <select
                value={localSettings.task_assignment.transcription}
                onChange={(e) => updateTaskAssignment('transcription', e.target.value)}
                className="appearance-none bg-ink-800 border border-ink-700 rounded-lg px-4 py-2 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer min-w-[220px]"
              >
                {getTranscriptionOptions().map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <ChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
            </div>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Summarization</span>
            </div>
            {renderAgentSelect('summarization', localSettings.task_assignment.summarization)}
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Visual Analysis</span>
            </div>
            {renderAgentSelect('visual_analysis', localSettings.task_assignment.visual_analysis)}
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Languages size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Translation</span>
            </div>
            {renderAgentSelect('translation', localSettings.task_assignment.translation ?? '')}
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ImageIcon size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Caption (Image → Prompt)</span>
            </div>
            {renderAgentSelect('caption', localSettings.task_assignment.caption ?? '')}
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Classification (Auto Tag)</span>
            </div>
            {renderAgentSelect('classification', localSettings.task_assignment.classification ?? '')}
          </div>
        </div>
        )}

        {/* Storyboard Tasks */}
        {taskTab === 'storyboard' && (
        <div className="px-6 pb-6 pt-2 space-y-5">
          {/* Image Generation — multi-select, only shows enabled providers */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <ImageIcon size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Image Generation</span>
              <span className="text-[11px] text-ink-600">(select per node in canvas)</span>
            </div>
            {(() => {
              const enabledImageProviders = IMAGE_PROVIDERS.filter(({ id }) => enabledSbProviders[id]);
              if (enabledImageProviders.length === 0) {
                return (
                  <p className="text-xs text-ink-600 py-2">
                    No image providers enabled. Enable them in AI Providers → Image Generation below.
                  </p>
                );
              }
              return (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {enabledImageProviders.map(({ id, name }) => {
                    const selected = (localSettings.task_assignment.image_generation ?? '').split(',').filter(Boolean).includes(id);
                    return (
                      <button
                        key={id}
                        type="button"
                        onClick={() => {
                          const current = (localSettings.task_assignment.image_generation ?? '').split(',').filter(Boolean);
                          const next = selected ? current.filter((x) => x !== id) : [...current, id];
                          updateTaskAssignment('image_generation' as keyof typeof localSettings.task_assignment, next.join(','));
                        }}
                        className={`px-3 py-2 rounded-lg text-xs font-medium transition-all border flex items-center gap-2 ${
                          selected
                            ? 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30'
                            : 'bg-ink-950 text-ink-500 border-ink-800 hover:border-ink-700 hover:text-ink-300'
                        }`}
                      >
                        {selected && <Check size={12} />}
                        {name}
                      </button>
                    );
                  })}
                </div>
              );
            })()}
          </div>

          {/* Script / Prompt — agent picker (script_generation routes to storyboard agent) */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">Script / Prompt</span>
            </div>
            {renderAgentSelect('script_generation', localSettings.task_assignment.script_generation ?? '')}
          </div>
        </div>
        )}
      </section>

      {/* Provider Cards Section */}
      <section className={`bg-ink-900 border border-ink-800 rounded-xl overflow-hidden transition-opacity ${localSettings.ai_enabled ? 'opacity-100' : 'opacity-40 pointer-events-none'}`}>
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50 flex items-center gap-3">
          <div className="p-2 bg-cyan-500/10 rounded-lg text-cyan-400">
            <Settings size={20} />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-ink-200">AI Providers</h2>
            <p className="text-xs text-ink-500">Configure API keys and connections for each provider</p>
          </div>
        </div>

        {/* Provider Type Tabs */}
        <div className="px-6 pt-4 pb-2">
          <div className="flex gap-1 p-1 bg-ink-950 rounded-lg">
            <button
              type="button"
              onClick={() => setProviderTab('text')}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                providerTab === 'text'
                  ? 'bg-ink-800 text-ink-100 shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              Text / LLM
            </button>
            <button
              type="button"
              onClick={() => setProviderTab('image')}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                providerTab === 'image'
                  ? 'bg-ink-800 text-ink-100 shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              Image Generation
            </button>
          </div>
        </div>

        {/* Text / LLM Providers */}
        {providerTab === 'text' && (
        <div className="p-6 pt-2 space-y-4">
          {Object.entries(PROVIDER_META).map(([providerKey, meta]) => {
            const config = getProviderConfig(providerKey);
            const colors = COLOR_MAP[meta.color] || COLOR_MAP.blue;
            const isLocal = meta.isLocal || false;
            const connStatus = connectionStatus[providerKey] || 'idle';

            return (
              <div
                key={providerKey}
                className={`bg-ink-950 border rounded-xl overflow-hidden transition-all ${
                  config.enabled ? colors.border : 'border-ink-800'
                }`}
              >
                {/* Provider Header */}
                <div className="px-6 py-4 flex items-center gap-4">
                  <div className={`p-2 rounded-lg ${colors.bg} ${colors.text}`}>
                    {meta.icon}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h4 className="font-semibold text-ink-200">{meta.name}</h4>
                      {meta.website && (
                        <a
                          href={meta.website}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Open ${meta.name} console`}
                          aria-label={`Open ${meta.name} console`}
                          className="text-ink-500 hover:text-ink-200 transition-colors"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                      {meta.badge && (
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${colors.badge}`}>
                          {meta.badge}
                        </span>
                      )}
                      {isLocal && connStatus === 'success' && (
                        <span className="flex items-center gap-1 text-xs text-green-400">
                          <Wifi size={12} />
                          Connected
                        </span>
                      )}
                      {isLocal && connStatus === 'error' && (
                        <span className="flex items-center gap-1 text-xs text-red-400">
                          <WifiOff size={12} />
                          Disconnected
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-ink-500 mt-0.5">{meta.description}</p>
                  </div>
                  {renderToggle(config.enabled, () => toggleProvider(providerKey))}
                </div>

                {/* Provider Config (expanded when enabled) */}
                {config.enabled && (
                  <div className="px-6 pb-5 pt-2 border-t border-ink-800/50 space-y-4 animate-in fade-in slide-in-from-top-2 duration-300">
                    {/* API Key (for cloud providers) */}
                    {!isLocal && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Key size={12} />
                          API Key
                        </label>
                        <div className="relative">
                          <input
                            type={showApiKeys[providerKey] ? 'text' : 'password'}
                            value={config.api_key || ''}
                            onChange={(e) => updateProviderField(providerKey, 'api_key', e.target.value)}
                            placeholder={`Enter your ${meta.name} API key`}
                            className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-10 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                          />
                          <button
                            type="button"
                            onClick={() => toggleShowApiKey(providerKey)}
                            className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-300 transition-colors"
                          >
                            {showApiKeys[providerKey] ? <EyeOff size={16} /> : <Eye size={16} />}
                          </button>
                        </div>
                      </div>
                    )}

                    {/* App ID (for volcengine) */}
                    {(meta as Record<string, unknown>).appIdField && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Key size={12} />
                          App ID
                        </label>
                        <input
                          type="text"
                          value={config.app_id || ''}
                          onChange={(e) => updateProviderField(providerKey, 'app_id' as keyof AIProviderConfig, e.target.value)}
                          placeholder="Enter App ID"
                          className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                        />
                      </div>
                    )}

                    {/* Server URL (for local providers) */}
                    {isLocal && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Server size={12} />
                          Server URL
                        </label>
                        <input
                          type="text"
                          value={config.base_url || meta.defaultBaseUrl || ''}
                          onChange={(e) => updateProviderField(providerKey, 'base_url', e.target.value)}
                          placeholder={meta.defaultBaseUrl}
                          className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                        />
                      </div>
                    )}

                    {/* OpenAI-specific model selectors */}
                    {providerKey === 'openai' && (
                      <>
                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-ink-400">Whisper Model</label>
                          <div className="relative">
                            <select
                              value={config.selected_model?.startsWith('whisper') ? config.selected_model : 'whisper-1'}
                              onChange={(e) => updateProviderField(providerKey, 'selected_model', e.target.value)}
                              className="w-full appearance-none bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                            >
                              {(meta.whisperModels || []).map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                            </select>
                            <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-ink-400">Summary Model</label>
                          <div className="relative">
                            <select
                              value={config.summary_model || 'gpt-4o-mini'}
                              onChange={(e) => updateProviderField(providerKey, 'summary_model' as keyof AIProviderConfig, e.target.value)}
                              className="w-full appearance-none bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                            >
                              {(meta.summaryModels || []).map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                            </select>
                            <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
                          </div>
                        </div>

                        <div className="space-y-1.5">
                          <label className="text-xs font-medium text-ink-400">Analysis Model</label>
                          <div className="relative">
                            <select
                              value={config.analysis_model || 'gpt-4o'}
                              onChange={(e) => updateProviderField(providerKey, 'analysis_model' as keyof AIProviderConfig, e.target.value)}
                              className="w-full appearance-none bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 pr-8 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 transition-colors cursor-pointer"
                            >
                              {(meta.analysisModels || []).map((m) => (
                                <option key={m} value={m}>{m}</option>
                              ))}
                            </select>
                            <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 pointer-events-none" />
                          </div>
                        </div>
                      </>
                    )}

                    {/* Generic model whitelist for non-OpenAI providers.
                        Chips = ``enabled_models`` (what gets exposed to
                        agent pickers). Test Connection populates the full
                        catalog (``config.models``); the user adds the
                        models they actually want from there. */}
                    {providerKey !== 'openai' && (
                      <EnabledModelsField
                        providerKey={providerKey}
                        enabledModels={config.enabled_models ?? (config.selected_model ? [config.selected_model] : [])}
                        catalog={config.models ?? meta.models}
                        onAdd={(model) => addEnabledModel(providerKey, model)}
                        onRemove={(model) => removeEnabledModel(providerKey, model)}
                      />
                    )}

                    {/* Test Connection button */}
                    {(isLocal || config.api_key) && (
                      <div className="pt-1">
                        <button
                          onClick={() => testConnection(providerKey)}
                          disabled={connStatus === 'testing'}
                          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 border ${
                            connStatus === 'success'
                              ? 'bg-green-500/10 text-green-400 border-green-500/30'
                              : connStatus === 'error'
                              ? 'bg-red-500/10 text-red-400 border-red-500/30'
                              : 'bg-ink-800 text-ink-300 border-ink-700 hover:bg-ink-700'
                          }`}
                        >
                          {connStatus === 'testing' && <Loader2 size={14} className="animate-spin" />}
                          {connStatus === 'success' && <Check size={14} />}
                          {connStatus === 'error' && <WifiOff size={14} />}
                          {connStatus === 'idle' && <Wifi size={14} />}
                          {connStatus === 'testing'
                            ? 'Testing...'
                            : connStatus === 'success'
                            ? 'Connected'
                            : connStatus === 'error'
                            ? 'Retry Connection'
                            : 'Test Connection'}
                        </button>
                        {connStatus === 'error' && connectionError[providerKey] && (
                          <p className="mt-2 text-xs text-red-400">{connectionError[providerKey]}</p>
                        )}
                        {connStatus === 'success' && config.models && config.models.length > 0 && (
                          <p className="mt-2 text-xs text-green-400/70">
                            Detected {config.models.length} model{config.models.length !== 1 ? 's' : ''} from server
                          </p>
                        )}
                        {connStatus === 'success' && quotaInfo[providerKey] && (
                          <p className="mt-1 text-xs text-ink-400">
                            Daily quota
                            {quotaInfo[providerKey]?.requests_remaining != null && (
                              <> — account: {quotaInfo[providerKey]?.requests_remaining}
                              /{quotaInfo[providerKey]?.requests_limit ?? '?'} requests left</>
                            )}
                            {quotaInfo[providerKey]?.model_requests_remaining != null && (
                              <> · this model: {quotaInfo[providerKey]?.model_requests_remaining}
                              /{quotaInfo[providerKey]?.model_requests_limit ?? '?'} left</>
                            )}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
        )}

        {/* Image Generation Providers */}
        {providerTab === 'image' && (
          <div className="p-6 pt-2">
            <StoryboardApiSettings />
          </div>
        )}
      </section>

      {/* Save Button */}
      <div className="flex flex-col items-end gap-2 pt-2 pb-4">
        {saveError && (
          <div className="flex items-center gap-2 text-sm text-red-400">
            <AlertCircle size={14} />
            {saveError}
          </div>
        )}
        <button
          onClick={handleSave}
          disabled={isSaving}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white px-8 py-3 rounded-lg font-medium transition-colors flex items-center gap-2 shadow-lg shadow-indigo-900/20"
        >
          {isSaving ? (
            <Loader2 size={18} className="animate-spin" />
          ) : saveSuccess ? (
            <Check size={18} />
          ) : (
            <Save size={18} />
          )}
          {saveSuccess ? 'Settings Saved!' : 'Save Settings'}
        </button>
      </div>

      {/* A: MCP Servers section — independent of LLM provider settings,
          but shown here since both are agent-runtime configs. */}
      <section className="mt-8 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
        <MCPServersPanel />
      </section>

      {/* G1-UI: Pending approvals — auto-hides when empty */}
      <section className="mt-8 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
        <ApprovalsPanel hideWhenEmpty />
      </section>

      {/* Phase 3: Token usage dashboard */}
      <section className="mt-8 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
        <TokenBillingDashboard />
      </section>

      {/* Canvas + AI Phase 2 closer: nous-center protocol probe */}
      <NousCenterVerifyPanel />
    </div>
  );
};

export default AISettings;
