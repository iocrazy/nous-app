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
  Lock,
  AlertTriangle,
  Terminal,
} from 'lucide-react';
import { AISettings as AISettingsType, AIProviderConfig, NousModelPublic, AILibraryAgent, AIGovernanceFlags } from '../types';
import {
  saveAISettings as saveAISettingsApi,
  testAIConnection as testAIConnectionApi,
  reportProviderHealth,
  getNousModels,
  getAIGovernance,
  GOVERNANCE_ALL_ALLOWED,
} from '../services/aiService';
import { useTranslation } from 'react-i18next';
import { visiblePlatformModels } from './AILibrary/agentEditorModel';
import { relativeTime } from '../utils/taskDisplay';
import { buildModelHealth, healthReasonKey } from '../utils/modelHealth';
import { suspectedNonChatKind, nonChatKindKey } from '../utils/nonChatModel';
import { aiLibraryService } from '../services/aiLibraryService';
import { HotwordChipInput } from './settings/HotwordChipInput';
import { ApprovalsPanel } from './ApprovalsPanel';
import { NousCenterVerifyPanel } from '../features/canvas-core/smart/NousCenterVerifyPanel';
import { AIHealthBoard } from './AIHealthBoard';
import { UiSelect } from './ui';

interface AISettingsProps {
  /** Which slice to render (settings-page tab split, 2026-08-26):
   *  'core' = intelligence + task assignment + health; 'providers' = the
   *  cloud API-key cards. Default 'all' keeps old callers working. */
  section?: 'all' | 'core' | 'providers';
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
    // No credential of its own (the paired daemon is the credential): hides
    // the API-key field and keeps Test Connection reachable without one.
    noApiKey?: boolean;
    // Explanatory line under the chips (i18n key, interpolates {{model}} =
    // first enabled chip). For providers whose capabilities are not the
    // chips themselves — Codex draws with a fixed image model.
    noteKey?: string;
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
    description: 'aiSettings.providerDesc.openai',
    icon: <Sparkles size={18} />,
    color: 'emerald',
    badge: 'aiSettings.badge.recommended',
    website: 'https://platform.openai.com/api-keys',
    models: ['gpt-5.6', 'gpt-5.4', 'gpt-5.2', 'gpt-5.2-mini', 'gpt-4o'],
    whisperModels: ['whisper-1'],
    summaryModels: ['gpt-4o-mini', 'gpt-4o', 'gpt-3.5-turbo'],
    analysisModels: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
  },
  // Codex on the user's OWN machine (paired daemon + ChatGPT login). Model ids
  // carry a ``codex:`` prefix so routing does not confuse them with the OpenAI
  // card's ``gpt-*`` (backend services/codex/provider_card.py). Test
  // Connection asks the daemon; success fills this catalog.
  'codex-local': {
    name: 'Codex (Local CLI)',
    description: 'aiSettings.providerDesc.codexLocal',
    icon: <Terminal size={18} />,
    color: 'emerald',
    badge: 'aiSettings.badge.localCli',
    noApiKey: true,
    noteKey: 'aiSettings.providerNote.codexLocal',
    website: 'https://chatgpt.com/codex',
    models: ['codex:gpt-6-astra', 'codex:gpt-5.6-sol', 'codex:gpt-5.5'],
  },
  deepseek: {
    name: 'DeepSeek',
    description: 'aiSettings.providerDesc.deepseek',
    website: 'https://platform.deepseek.com/api_keys',
    icon: <Zap size={18} />,
    color: 'blue',
    models: ['deepseek-chat', 'deepseek-reasoner'],
    summaryModels: ['deepseek-chat', 'deepseek-reasoner'],
  },
  doubao: {
    name: 'Doubao',
    description: 'aiSettings.providerDesc.doubao',
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
    description: 'aiSettings.providerDesc.minimax',
    website: 'https://platform.minimaxi.com/user-center/basic-information/interface-key',
    icon: <MessageSquare size={18} />,
    color: 'amber',
    models: ['MiniMax-M2.5', 'MiniMax-M2.5-highspeed', 'MiniMax-M2.1', 'MiniMax-M2'],
    summaryModels: ['MiniMax-M2.5', 'MiniMax-M2.5-highspeed', 'MiniMax-M2.1'],
  },
  kimi: {
    name: 'Kimi',
    description: 'aiSettings.providerDesc.kimi',
    website: 'https://platform.moonshot.cn/console/api-keys',
    icon: <Moon size={18} />,
    color: 'teal',
    models: ['kimi-k2.5', 'kimi-k2', 'moonshot-v1-128k', 'moonshot-v1-32k', 'moonshot-v1-8k'],
    summaryModels: ['kimi-k2.5', 'kimi-k2', 'moonshot-v1-32k'],
    analysisModels: ['kimi-k2.5'],
  },
  qwen: {
    name: 'Qwen (Bailian)',
    description: 'aiSettings.providerDesc.qwen',
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
    description: 'aiSettings.providerDesc.modelscope',
    icon: <Brain size={18} />,
    color: 'violet',
    badge: 'aiSettings.badge.freeTier',
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
    description: 'aiSettings.providerDesc.volcengine',
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
    description: 'aiSettings.providerDesc.ollama',
    icon: <Server size={18} />,
    color: 'orange',
    isLocal: true,
    defaultBaseUrl: 'http://localhost:11434',
    website: 'https://ollama.com/download',
    models: ['qwen2.5:7b', 'qwen2.5:14b', 'llama3.1:8b', 'llama3.1:70b', 'mistral:7b', 'gemma2:9b'],
  },
  lmstudio: {
    name: 'LM Studio',
    description: 'aiSettings.providerDesc.lmstudio',
    icon: <Server size={18} />,
    color: 'pink',
    isLocal: true,
    defaultBaseUrl: 'http://localhost:1234',
    website: 'https://lmstudio.ai',
    models: [],
  },
};

const LANGUAGE_OPTIONS = [
  { value: 'auto', label: 'aiSettings.lang.auto' },
  { value: 'en', label: 'aiSettings.lang.en' },
  { value: 'zh', label: 'aiSettings.lang.zh' },
  { value: 'ja', label: 'aiSettings.lang.ja' },
  { value: 'ko', label: 'aiSettings.lang.ko' },
  { value: 'es', label: 'aiSettings.lang.es' },
  { value: 'fr', label: 'aiSettings.lang.fr' },
  { value: 'de', label: 'aiSettings.lang.de' },
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
    badge: 'bg-amber-500/20 text-warn border-amber-500/30',
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

type TaskTab = 'media' | 'storyboard';

// Display order for the platform-models card: llm → asr → embedding → image,
// with tts/video trailing. Unknown types fall to the end (99).
const NOUS_TYPE_ORDER: Record<string, number> = {
  llm: 0,
  asr: 1,
  embedding: 2,
  image: 3,
  tts: 4,
  video: 5,
};

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
  /**
   * The model this provider's legacy single-model paths actually use —
   * ``selected_model``. Summarization USED to resolve exactly this value,
   * which is how an embedding model ended up being POSTed to
   * /chat/completions in production; as of the 2026-08-20 收口 it reads the
   * assigned agent's model instead (backend resolve_task_ai_config), so this
   * field no longer steers summarization.
   */
  selectedModel?: string;
  /**
   * Already-localized warning for a model whose NAME suggests it cannot chat,
   * or null. A guess, so it marks and never blocks — see utils/nonChatModel.
   */
  nonChatWarning?: (modelId: string) => string | null;
}> = ({
  providerKey,
  enabledModels,
  catalog,
  onAdd,
  onRemove,
  selectedModel,
  nonChatWarning,
}) => {
  const { t } = useTranslation();
  const [picking, setPicking] = useState(false);
  const [filter, setFilter] = useState('');
  const typed = filter.trim();

  const remaining = useMemo(
    () =>
      catalog.filter(
        (m) => !enabledModels.includes(m) && m.toLowerCase().includes(filter.toLowerCase()),
      ),
    [catalog, enabledModels, filter],
  );

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium text-ink-400">{t('aiSettings.enabledModels')}</label>
      <div className="flex flex-wrap items-center gap-2">
        {enabledModels.map((m) => {
          const suspect = nonChatWarning?.(m) ?? null;
          return (
          <span
            key={m}
            data-testid={suspect ? 'non-chat-chip' : undefined}
            title={suspect ?? undefined}
            className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-mono ${
              suspect
                ? 'bg-danger-soft border border-danger-line text-danger'
                : 'bg-[var(--accent-soft)] border border-[var(--accent-border)] text-[var(--accent-text)]'
            }`}
          >
            {suspect && <AlertTriangle size={11} className="shrink-0" aria-hidden />}
            {m}
            <button
              type="button"
              onClick={() => onRemove(m)}
              className={`transition-opacity hover:opacity-70 ${
                suspect ? 'text-danger' : 'text-[var(--accent-text)]'
              }`}
              aria-label={t('aiSettings.removeModel', { model: m })}
            >
              <X size={12} />
            </button>
          </span>
          );
        })}
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
            {t('aiSettings.addModel')}
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
              placeholder={t('aiSettings.filterModels')}
              className="flex-1 bg-transparent text-xs text-ink-200 placeholder:text-ink-600 focus:outline-none"
            />
            <button
              type="button"
              onClick={() => setPicking(false)}
              className="text-ink-500 hover:text-ink-200 transition-colors"
              aria-label={t('aiSettings.closePicker')}
            >
              <X size={14} />
            </button>
          </div>
          {/* A name the catalog does not list (providers rename models faster
              than any list keeps up) can still be enabled by typing it. */}
          {typed && !enabledModels.includes(typed) && !catalog.includes(typed) && (
            <button
              type="button"
              data-testid="add-custom-model"
              onClick={() => {
                onAdd(typed);
                setFilter('');
              }}
              className="mb-1 w-full text-left rounded px-2 py-1 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-50 transition-colors"
            >
              {t('aiSettings.addTyped', { model: typed })}
            </button>
          )}
          {catalog.length === 0 && !typed ? (
            <div className="text-xs text-ink-500 px-1 py-2">
              {t('aiSettings.catalogEmpty')}
            </div>
          ) : remaining.length === 0 ? (
            <div className="text-xs text-ink-500 px-1 py-2">
              {filter ? t('aiSettings.noMatches') : t('aiSettings.allEnabled')}
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

      {/* The model the provider's single-model paths (summarization, legacy
          task_assignment) will actually call. ``selected_model`` is what the
          backend reads; the [0] fallback mirrors this page's own seeding rule
          (addEnabledModel / removeEnabledModel keep it pointing at the first
          chip) so a legacy account without it still gets warned. */}
      {(() => {
        const effective = selectedModel || enabledModels[0] || '';
        const warning = effective ? nonChatWarning?.(effective) ?? null : null;
        if (!warning) return null;
        return (
          <div
            data-testid="non-chat-warnline"
            role="alert"
            className="flex items-start gap-1.5 rounded-md border border-danger-line bg-danger-soft px-2 py-1.5 text-[11px] text-danger"
          >
            <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden />
            <span>{warning}</span>
          </div>
        );
      })()}

      <p className="text-[11px] text-ink-500">
        {t('aiSettings.enabledModelsHint')}{' '}
        <span className="font-mono">{providerKey}</span>
      </p>
    </div>
  );
};

/** Small note shown in place of a locked module's Task Assignment row. */
const ManagedNote: React.FC = () => {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-1.5 text-xs text-ink-500 py-1">
      <Lock size={11} className="text-ink-600 shrink-0" aria-hidden />
      <span>{t('aiSettings.managedByAdmin')}</span>
    </div>
  );
};

export const AISettings: React.FC<AISettingsProps> = ({ settings, onSave, section = 'all' }) => {
  // Every user-facing string on this page resolves through i18n (2026-08-26):
  // labels, placeholders, option text and the model-health / non-chat warnings
  // alike. Provider brand names, model ids and upstream error text stay verbatim
  // — they are identifiers, not copy.
  const { t } = useTranslation();
  const [taskTab, setTaskTab] = useState<TaskTab>('media');
  const [governance, setGovernance] = useState<AIGovernanceFlags>(GOVERNANCE_ALL_ALLOWED);
  const [localSettings, setLocalSettings] = useState<AISettingsType>(() => ({
    ...settings,
  }));
  const [showApiKeys, setShowApiKeys] = useState<Record<string, boolean>>({});
  const [connectionStatus, setConnectionStatus] = useState<Record<string, 'idle' | 'testing' | 'success' | 'error'>>({});
  const [connectionError, setConnectionError] = useState<Record<string, string>>({});
  // ISO timestamp of the last connection test per provider (persisted server-
  // side, seeded from settings.provider_health on load, updated live on test).
  const [lastTested, setLastTested] = useState<Record<string, string>>({});
  // Daily-quota counters from Test Connection (ModelScope rate-limit headers).
  const [quotaInfo, setQuotaInfo] = useState<Record<string, Record<string, number> | undefined>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [nousModels, setNousModels] = useState<NousModelPublic[]>([]);
  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  // The three async option sources (platform models, agents, governance) hydrate
  // after mount. Until ALL have settled, a task-assignment picker whose stored
  // value isn't yet in its (still-empty) option list would silently display the
  // first option — a wrong provider/agent that snaps to the real value once data
  // lands. These "settled" flags gate the pickers behind a stable placeholder so
  // they never flash a value the user didn't choose. Settled = resolved OR
  // rejected (fail-open governance still counts as ready).
  const [nousModelsLoaded, setNousModelsLoaded] = useState(false);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [governanceLoaded, setGovernanceLoaded] = useState(false);
  const optionsReady = nousModelsLoaded && agentsLoaded && governanceLoaded;
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

  // Seed the connection-test UI from persisted provider_health so a reload
  // restores the last "Connected / Failed / Last tested" state. Live results
  // (prev) win over the seed — a fresh test in this session is never clobbered
  // by a later prop refresh.
  useEffect(() => {
    const health = settings.provider_health;
    if (!health) return;
    const statusSeed: Record<string, 'success' | 'error'> = {};
    const errorSeed: Record<string, string> = {};
    const testedSeed: Record<string, string> = {};
    for (const [key, entry] of Object.entries(health)) {
      if (!entry) continue;
      statusSeed[key] = entry.status === 'ok' ? 'success' : 'error';
      if (entry.detail) errorSeed[key] = entry.detail;
      if (entry.tested_at) testedSeed[key] = entry.tested_at;
    }
    setConnectionStatus((prev) => ({ ...statusSeed, ...prev }));
    setConnectionError((prev) => ({ ...errorSeed, ...prev }));
    setLastTested((prev) => ({ ...testedSeed, ...prev }));
  }, [settings.provider_health]);

  useEffect(() => {
    let cancelled = false;
    getNousModels()
      .then((list) => {
        if (!cancelled) setNousModels(list);
      })
      .catch((err) => {
        console.error('[AISettings] getNousModels failed:', err);
      })
      .finally(() => {
        if (!cancelled) setNousModelsLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch per-module governance flags once on mount.
  // Fail-open: any error leaves governance as GOVERNANCE_ALL_ALLOWED (all true).
  useEffect(() => {
    let cancelled = false;
    getAIGovernance()
      .then((flags) => {
        if (!cancelled) setGovernance(flags);
      })
      .catch((err) => {
        console.error('[AISettings] governance fetch failed — defaulting to all-allowed:', err);
      })
      .finally(() => {
        if (!cancelled) setGovernanceLoaded(true);
      });
    return () => {
      cancelled = true;
    };
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
      })
      .finally(() => {
        if (!cancelled) setAgentsLoaded(true);
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

  // Update transcription hotwords (ASR domain hints — names, terms)
  const setTranscriptionHotwords = (value: string) => {
    editLocalSettings((prev) => ({
      ...prev,
      transcription_hotwords: value,
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

  // --- Platform ("nous") pseudo-provider: user-side visibility controls ---
  // Persisted like any other provider at localSettings.providers.nous, shape
  // { enabled: boolean; disabled_models: string[] }. Blacklist (disabled_models)
  // semantics: a model the admin adds later stays visible until the user opts
  // it out — never hidden by default. Master toggle defaults ON (config absent
  // or enabled !== false).
  const nousUserEnabled = localSettings.providers.nous?.enabled !== false;

  // The set of platform models the user has hidden from the pickers.
  const nousDisabledModels = localSettings.providers.nous?.disabled_models ?? [];

  // Single derived list every user-facing picker consumes so no consumption
  // point can drift: admin master switch on AND user master toggle on AND the
  // model is not in the user's blacklist. Per-module governance (nous_modules)
  // is applied on top of this at each picker.
  // Platform-model self-check (spec 2026-08-14 §F2). The backend probes every
  // enabled platform model hourly; until now the verdict never left the admin
  // surface, so picking a model whose probe was failing looked identical to
  // picking a healthy one — right up until the job failed.
  //
  // A failing model is MARKED, never removed and never disabled: the probe has
  // returned a false negative in production (2026-08-14), so it advises rather
  // than vetoes. `relativeTime` (already imported for this file's English UI)
  // carries the check age — hourly cadence means a 50-minute-old verdict is
  // not a statement about right now.
  //
  // The verdict now carries WHY (spec 2026-08-14 reason-code, backend mig 427).
  // "Timed out" and "rate limited" ask the user for opposite things — wait vs
  // go deal with a quota — and under #1838 both read as the same bare red dot.
  // The reason is a closed enum, never the probe's raw text: that text embeds
  // the upstream host and private base_url and never leaves admin.
  const nousHealth = useMemo(() => buildModelHealth(nousModels), [nousModels]);
  const nousHealthWarning = (modelName: string): string | null => {
    const health = nousHealth[modelName];
    if (health?.status !== 'fail') return null;
    const checked = relativeTime(health.testedAt ?? undefined);
    const reasonKey = healthReasonKey(health.code);
    // No key = a row probed before the column existed, or a code newer than
    // this build. Both fall back to the original reason-less wording.
    if (!reasonKey) {
      return checked
        ? t('aiSettings.modelHealthFailedAgo', { ago: checked })
        : t('aiSettings.modelHealthFailed');
    }
    const reason = t(reasonKey);
    return checked
      ? t('aiSettings.modelHealthFailedReasonAgo', { reason, ago: checked })
      : t('aiSettings.modelHealthFailedReason', { reason });
  };

  // BYOK model guard (2026-08-16 incident). A user pruned doubao's enabled
  // models down to `doubao-embedding-vision-251215`; summarization takes that
  // provider's selected_model, so every summary afterwards sent an embedding
  // model to /chat/completions and failed — with nothing on this page saying so.
  //
  // Unlike the platform card above there is no health signal to read here: a
  // BYOK catalog is the provider's own /models output, raw upstream ids with no
  // type field, and the platform catalog's health is keyed by its own alias
  // (`name`), not by the upstream id — matching the two would attach one key's
  // verdict to a different key's model. So this warns from the NAME only, and
  // says nothing whenever the name doesn't clearly say (utils/nonChatModel).
  //
  // Marks, never blocks — same call as the health line: it is a guess, and the
  // user may know better.
  const nonChatWarning = (modelId: string): string | null => {
    const kind = suspectedNonChatKind(modelId);
    if (!kind) return null;
    return t('aiSettings.nonChatSelected', {
      model: modelId,
      kind: t(nonChatKindKey(kind)),
    });
  };

  const nousConfig = localSettings.providers.nous;
  const visibleNousModels = useMemo(() => {
    if (!governance.nous_enabled) return [];
    // Same predicate the agent editor uses (one management entry, one rule).
    return visiblePlatformModels(nousModels, nousConfig);
  }, [governance.nous_enabled, nousConfig, nousModels]);

  // Flip the platform-card master switch. Absent config => currently ON, so the
  // first toggle turns it off.
  const toggleNousMaster = () => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers.nous ?? { enabled: true };
      providers.nous = { ...current, enabled: current.enabled === false };
      return { ...prev, providers };
    });
  };

  // Toggle one platform model in/out of the user's blacklist.
  const toggleNousModel = (modelName: string) => {
    editLocalSettings((prev) => {
      const providers = { ...prev.providers };
      const current = providers.nous ?? { enabled: true };
      const disabled = current.disabled_models ?? [];
      const next = disabled.includes(modelName)
        ? disabled.filter((n) => n !== modelName)
        : [...disabled, modelName];
      providers.nous = { ...current, disabled_models: next };
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
    // Stamp the test time locally so "Last tested ..." updates immediately;
    // the backend persists the authoritative copy (server-side for cloud,
    // via reportProviderHealth for local).
    const markTested = () =>
      setLastTested((prev) => ({ ...prev, [providerKey]: new Date().toISOString() }));

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
          let modelCount = 0;
          if (data?.data && Array.isArray(data.data)) {
            const modelIds = data.data.map((m: { id: string }) => m.id);
            modelCount = modelIds.length;
            if (modelIds.length > 0) {
              updateProviderField(providerKey, 'models', modelIds);
              if (!provider.selected_model || !modelIds.includes(provider.selected_model)) {
                updateProviderField(providerKey, 'selected_model', modelIds[0]);
              }
            }
          }
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'success' }));
          markTested();
          // The backend can't reach the user's localhost — report the outcome
          // so it persists across reloads. Fire-and-forget.
          reportProviderHealth(
            providerKey,
            'ok',
            modelCount > 0 ? `${modelCount} models available` : 'Connection OK',
          ).catch((e) => console.error('[AISettings] reportProviderHealth failed:', e));
        } else {
          // Two audiences, two strings. `reportDetail` is PERSISTED by the
          // backend (user_settings.provider_health) and later stitched into
          // English operator hints (ai_health.py), so it must stay an English
          // literal — same style as the 'Connection OK' success branch above.
          // Only `displayDetail` is read by this page, so only that one is
          // localized. Sending the localized copy to the backend would write
          // the user's UI language into shared health data.
          const reportDetail = `Server responded with status ${response.status}`;
          const displayDetail = t('aiSettings.errServerStatus', {
            status: response.status,
          });
          setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'error' }));
          setConnectionError((prev) => ({ ...prev, [providerKey]: displayDetail }));
          markTested();
          reportProviderHealth(providerKey, 'fail', reportDetail).catch((e) =>
            console.error('[AISettings] reportProviderHealth failed:', e),
          );
        }
      } else {
        // Use backend API for cloud providers (backend persists the outcome).
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
          setConnectionError((prev) => ({
            ...prev,
            [providerKey]: result.error || t('aiSettings.errConnectionFailed'),
          }));
        }
        markTested();
      }
    } catch (err) {
      // Same split as the non-ok branch above: the persisted copy stays
      // English, only the on-screen copy goes through i18n. An Error message
      // is upstream text and identical on both paths.
      const reportDetail = err instanceof Error ? err.message : 'Connection failed';
      const displayDetail =
        err instanceof Error ? err.message : t('aiSettings.errConnectionFailed');
      setConnectionStatus((prev) => ({ ...prev, [providerKey]: 'error' }));
      setConnectionError((prev) => ({ ...prev, [providerKey]: displayDetail }));
      markTested();
      // Local test threw (timeout / network) — the backend never saw it, so
      // report the failure here. Cloud failures are already persisted server-side.
      if (isLocal) {
        reportProviderHealth(providerKey, 'fail', reportDetail).catch((e) =>
          console.error('[AISettings] reportProviderHealth failed:', e),
        );
      }
    }
    // `t` belongs here: the callback localizes the two display strings above.
    // Nothing subscribes to this callback's identity (it is only wired to the
    // Test Connection onClick), so re-creating it on a language switch costs
    // nothing and re-running is impossible.
  }, [localSettings.providers, t]);

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
      setSaveError(err instanceof Error ? err.message : t('aiSettings.errSaveFailed'));
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

    // Append Nous platform ASR models. visibleNousModels already folds in the
    // admin master switch + the user's platform-card master toggle + per-model
    // blacklist; here we only add the per-module governance gate.
    const nousAllowed = governance.nous_modules?.transcription ?? true;
    const matchingNousModels = nousAllowed
      ? visibleNousModels.filter((m) => m.type === 'asr')
      : [];
    for (const model of matchingNousModels) {
      const pricingLabel =
        model.pricing_type === 'per_hour'
          ? t('aiSettings.pricingPerHour', { value: model.pricing_value })
          : model.pricing_type === 'per_request'
            ? t('aiSettings.pricingPerRequest', { value: model.pricing_value })
            : t('aiSettings.pricingPerTokens', { value: model.pricing_value });
      const warning = nousHealthWarning(model.name);
      options.push({
        value: `nous:${model.name}`,
        label:
          t('aiSettings.platformAsrOption', {
            name: model.display_name,
            pricing: pricingLabel,
          }) + (warning ? ` — ${warning}` : ''),
      });
    }

    if (options.length === 0) {
      options.push({ value: '', label: t('aiSettings.noProviderEnabled') });
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
      label: a.is_system_preset
        ? t('aiSettings.agentSystem', { name: a.name })
        : t('aiSettings.agentMine', { name: a.name }),
      group: a.is_system_preset ? 'system' : 'mine',
    }));
  };

  // Backend default agent slug per task — the agent the backend resolver falls
  // back to when task_assignment[taskKey] is unset. Source of truth is
  // backend/app/services/ai/providers/ai_provider_helpers.py: the
  // DEFAULT_*_AGENT_SLUG constants (L368-372) and the resolve_*_provider_config
  // wrappers that pass them (L974-1030). Both sides must be kept in sync — a
  // wrong entry here mislabels what actually runs.
  // `summarization` joined this table on 2026-08-20: its workflow used to scan
  // a hardcoded provider priority and read no agent row at all (so the picker
  // above wrote a value nothing consumed); it now resolves through the same
  // resolve_task_ai_config path as the others, defaulting to `summarize`.
  const TASK_DEFAULT_AGENT_SLUG: Partial<
    Record<keyof AISettingsType['task_assignment'], string>
  > = {
    summarization: 'summarize',
    visual_analysis: 'analyze',
    translation: 'translate',
    caption: 'caption',
    classification: 'classify',
    script_generation: 'script_ai',
  };

  // Label for the explicit "unset" option. Without it the picker renders no
  // option matching value='' and UiSelect falls through to the first option in
  // the list, so an unassigned task looked assigned to whatever agent happened
  // to sort first — and picking that agent fired no change event, making the
  // phantom assignment unsavable.
  const getDefaultAgentLabel = (
    taskKey: keyof AISettingsType['task_assignment'],
  ): string => {
    const slug = TASK_DEFAULT_AGENT_SLUG[taskKey];
    if (!slug) return t('aiSettings.defaultSystem');
    const agent = agents.find((a) => a.slug === slug);
    if (!agent) return t('aiSettings.defaultSlug', { slug });
    return t('aiSettings.defaultAgent', {
      name: agent.is_system_preset
        ? t('aiSettings.agentSystem', { name: agent.name })
        : agent.name,
    });
  };

  // Disabled placeholder select shown while the async option sources are still
  // settling. Renders a single synthetic option matching the stored value so the
  // picker echoes the user's saved choice (raw value, or "Loading…" if unset)
  // instead of silently snapping to the first available option — the source of
  // the "shows Volcengine bigasr then jumps to moss" flash. Swaps seamlessly to
  // the real list once optionsReady flips.
  const renderLoadingSelect = (currentValue: string, className = 'min-w-[220px]') => (
    <UiSelect value={currentValue} disabled aria-busy="true" className={className}>
      <option value={currentValue}>{currentValue || t('aiSettings.loading')}</option>
    </UiSelect>
  );

  // Render an agent <select> with optgroup (system vs mine) + legacy value fallback.
  const renderAgentSelect = (
    taskKey: keyof AISettingsType['task_assignment'],
    currentValue: string,
  ) => {
    // Hold a stable placeholder until agents + platform models + governance
    // have all settled, so the picker never flashes a wrong agent/legacy state.
    if (!optionsReady) return renderLoadingSelect(currentValue);
    const options = getAgentOptions();
    const systemOptions = options.filter((o) => o.group === 'system');
    const mineOptions = options.filter((o) => o.group === 'mine');
    // Platform (Nous) LLM models are directly selectable per task — same as
    // the ASR picker. visibleNousModels already folds in the admin master switch
    // + the user's platform-card master toggle + per-model blacklist; here we
    // only add the per-module governance gate.
    const nousAllowed = governance.nous_modules?.[taskKey] ?? true;
    const nousLlmOptions = nousAllowed
      ? visibleNousModels
          .filter((m) => m.type === 'llm')
          .map((m) => {
            const warning = nousHealthWarning(m.name);
            return {
              value: `nous:${m.name}`,
              label:
                t('aiSettings.platformLlmOption', { name: m.display_name }) +
                (warning ? ` — ${warning}` : ''),
            };
          })
      : [];
    const knownSlugs = new Set([
      ...options.map((o) => o.value),
      ...nousLlmOptions.map((o) => o.value),
    ]);
    const isLegacy = currentValue !== '' && !knownSlugs.has(currentValue);

    return (
      <UiSelect
        value={currentValue}
        onChange={(e) => updateTaskAssignment(taskKey, e.target.value)}
        className="min-w-[220px]"
      >
          {options.length === 0 && nousLlmOptions.length === 0 ? (
            <option value="">{t('aiSettings.noAgents')}</option>
          ) : (
            <option value="">{getDefaultAgentLabel(taskKey)}</option>
          )}
          {isLegacy && (
            <option value={currentValue}>
              {t('aiSettings.legacyOption', { value: currentValue })}
            </option>
          )}
          {systemOptions.length > 0 && (
            <optgroup label={t('aiSettings.groupSystem')}>
              {systemOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </optgroup>
          )}
          {mineOptions.length > 0 && (
            <optgroup label={t('aiSettings.groupMine')}>
              {mineOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </optgroup>
          )}
          {nousLlmOptions.length > 0 && (
            <optgroup label={t('aiSettings.groupPlatform')}>
              {nousLlmOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </optgroup>
          )}
      </UiSelect>
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
      {section !== 'providers' && (
      <>
      {/* Master AI Toggle Section */}
      <section className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50 flex items-center gap-3">
          <div className="p-2 bg-[var(--accent-soft)] rounded-lg text-[var(--accent-text)]">
            <Brain size={20} />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-ink-200">{t('aiSettings.intelligenceTitle')}</h2>
            <p className="text-xs text-ink-500">{t('aiSettings.intelligenceDesc')}</p>
          </div>
        </div>

        <div className="p-6 space-y-5">
          {/* AI Enabled Toggle */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`w-2.5 h-2.5 rounded-full ${localSettings.ai_enabled ? 'bg-green-400 shadow-lg shadow-green-400/30' : 'bg-ink-600'}`} />
              <span className="font-medium text-ink-200">{t('aiSettings.aiEnabled')}</span>
            </div>
            <button
              onClick={toggleAIEnabled}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 ${
                localSettings.ai_enabled
                  ? 'bg-red-500/10 text-red-400 border border-red-500/30 hover:bg-red-500/20'
                  : 'bg-[var(--accent-soft)] text-[var(--accent-text)] border border-[var(--accent-border)] hover:bg-[var(--accent-soft)]'
              }`}
            >
              {localSettings.ai_enabled ? (
                <>
                  <ToggleRight size={16} />
                  {t('aiSettings.disableAi')}
                </>
              ) : (
                <>
                  <ToggleLeft size={16} />
                  {t('aiSettings.enableAi')}
                </>
              )}
            </button>
          </div>

          {/* Per-resource AI tasks now run via the intent tags (Transcript /
              Summary / Analyze) attached on the parse page or MediaCard.
              Replaces the old global Auto-Transcribe / Auto-Summarize toggles. */}
          <div className={`space-y-4 transition-opacity ${localSettings.ai_enabled ? 'opacity-100' : 'opacity-40 pointer-events-none'}`}>
            <div className="flex items-center justify-between py-2">
              <span className="text-sm text-ink-300">{t('aiSettings.preferredLanguage')}</span>
              <UiSelect
                value={localSettings.preferred_language}
                onChange={(e) => setPreferredLanguage(e.target.value)}
                disabled={!localSettings.ai_enabled}
              >
                {LANGUAGE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {t(opt.label)}
                  </option>
                ))}
              </UiSelect>
            </div>

            {/* Transcription hotwords — a content hint (people, terms) applied
                on every ASR run, even under governance lock. */}
            <div className="flex flex-col gap-1.5 py-2">
              <span className="text-sm text-ink-300">{t('aiSettings.hotwordsLabel')}</span>
              <HotwordChipInput
                value={localSettings.transcription_hotwords ?? ''}
                onChange={setTranscriptionHotwords}
                disabled={!localSettings.ai_enabled}
                placeholder={t('aiSettings.hotwordsPlaceholder')}
              />
              <span className="text-xs text-ink-500">
                {t('aiSettings.hotwordsHint')}
              </span>
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
            <h2 className="font-semibold text-ink-200">{t('aiSettings.taskAssignTitle')}</h2>
            <p className="text-xs text-ink-500">{t('aiSettings.taskAssignDesc')}</p>
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
                  ? 'bg-indigo-600 text-white shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              {t('aiSettings.tabMedia')}
            </button>
            <button
              type="button"
              onClick={() => setTaskTab('storyboard')}
              className={`flex-1 px-3 py-2 rounded-md text-xs font-medium transition-colors ${
                taskTab === 'storyboard'
                  ? 'bg-indigo-600 text-white shadow-sm'
                  : 'text-ink-500 hover:text-ink-300'
              }`}
            >
              {t('aiSettings.tabStoryboard')}
            </button>
          </div>
        </div>

        {/* Media Tasks */}
        {taskTab === 'media' && (
        <div className="px-6 pb-6 pt-2 space-y-5">
          {/* Transcription — hidden when governance.transcription is false */}
          {governance.transcription ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskTranscription')}</span>
            </div>
            {!optionsReady ? (
              renderLoadingSelect(localSettings.task_assignment.transcription)
            ) : (
            <UiSelect
              value={localSettings.task_assignment.transcription}
              onChange={(e) => updateTaskAssignment('transcription', e.target.value)}
              className="min-w-[220px]"
            >
              {getTranscriptionOptions().map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </UiSelect>
            )}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskTranscription')}</span>
            </div>
            <ManagedNote />
          </div>
          )}

          {/* Summarization — hidden when governance.summarization is false */}
          {governance.summarization ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskSummarization')}</span>
            </div>
            {renderAgentSelect('summarization', localSettings.task_assignment.summarization)}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Sparkles size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskSummarization')}</span>
            </div>
            <ManagedNote />
          </div>
          )}

          {/* Visual Analysis — hidden when governance.visual_analysis is false */}
          {governance.visual_analysis ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskVisualAnalysis')}</span>
            </div>
            {renderAgentSelect('visual_analysis', localSettings.task_assignment.visual_analysis)}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskVisualAnalysis')}</span>
            </div>
            <ManagedNote />
          </div>
          )}

          {/* Translation — hidden when governance.translation is false */}
          {governance.translation ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Languages size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskTranslation')}</span>
            </div>
            {renderAgentSelect('translation', localSettings.task_assignment.translation ?? '')}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Languages size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskTranslation')}</span>
            </div>
            <ManagedNote />
          </div>
          )}

          {/* Caption — hidden when governance.caption is false */}
          {governance.caption ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ImageIcon size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskCaption')}</span>
            </div>
            {renderAgentSelect('caption', localSettings.task_assignment.caption ?? '')}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <ImageIcon size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskCaption')}</span>
            </div>
            <ManagedNote />
          </div>
          )}

          {/* Classification — hidden when governance.classification is false */}
          {governance.classification ? (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskClassification')}</span>
            </div>
            {renderAgentSelect('classification', localSettings.task_assignment.classification ?? '')}
          </div>
          ) : (
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Search size={16} className="text-ink-500" />
              <span className="text-sm font-medium text-ink-500">{t('aiSettings.taskClassification')}</span>
            </div>
            <ManagedNote />
          </div>
          )}
        </div>
        )}

        {/* Storyboard Tasks */}
        {taskTab === 'storyboard' && (
        <div className="px-6 pb-6 pt-2 space-y-5">
          {/* Script / Prompt — agent picker (script_generation routes to storyboard agent) */}
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-ink-400" />
              <span className="text-sm font-medium text-ink-300">{t('aiSettings.taskScript')}</span>
            </div>
            {renderAgentSelect('script_generation', localSettings.task_assignment.script_generation ?? '')}
          </div>
        </div>
        )}
      </section>

      {/* Capability health board — what each feature actually uses */}
      <AIHealthBoard />
      </>
      )}

      {/* Provider Cards Section — hidden only when ALL governance-controlled
          modules are locked (chat + 5 task modules). When every module is
          admin-managed, no user BYOK key has any effect so the section is
          irrelevant to end-users. Partial-lock keeps it visible. */}
      {section !== 'core' && !(
        !governance.chat &&
        !governance.transcription &&
        !governance.translation &&
        !governance.visual_analysis &&
        !governance.caption &&
        !governance.classification &&
        !governance.summarization
      ) && (
      <section className={`bg-ink-900 border border-ink-800 rounded-xl overflow-hidden transition-opacity ${localSettings.ai_enabled ? 'opacity-100' : 'opacity-40 pointer-events-none'}`}>
        <div className="px-6 py-4 border-b border-ink-800 bg-ink-900/50 flex items-center gap-3">
          <div className="p-2 bg-cyan-500/10 rounded-lg text-cyan-400">
            <Settings size={20} />
          </div>
          <div className="flex-1">
            <h2 className="font-semibold text-ink-200">{t('aiSettings.providersTitle')}</h2>
            <p className="text-xs text-ink-500">{t('aiSettings.providersDesc')}</p>
          </div>
        </div>

        {/* Text / LLM Providers */}
        <div className="p-6 pt-2 space-y-4">
          {Object.entries(PROVIDER_META).map(([providerKey, meta]) => {
            const config = getProviderConfig(providerKey);
            const colors = COLOR_MAP[meta.color] || COLOR_MAP.blue;
            const isLocal = meta.isLocal || false;
            const connStatus = connectionStatus[providerKey] || 'idle';

            return (
              <div
                key={providerKey}
                data-testid={`provider-card-${providerKey}`}
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
                          title={t('aiSettings.openConsole', { name: meta.name })}
                          aria-label={t('aiSettings.openConsole', { name: meta.name })}
                          className="text-ink-500 hover:text-ink-200 transition-colors"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                      {meta.badge && (
                        <span className={`text-xs px-2 py-0.5 rounded-full border ${colors.badge}`}>
                          {t(meta.badge)}
                        </span>
                      )}
                      {isLocal && connStatus === 'success' && (
                        <span className="flex items-center gap-1 text-xs text-green-400">
                          <Wifi size={12} />
                          {t('aiSettings.connected')}
                        </span>
                      )}
                      {isLocal && connStatus === 'error' && (
                        <span className="flex items-center gap-1 text-xs text-red-400">
                          <WifiOff size={12} />
                          {t('aiSettings.disconnected')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-ink-500 mt-0.5">{t(meta.description)}</p>
                  </div>
                  {renderToggle(config.enabled, () => toggleProvider(providerKey))}
                </div>

                {/* Provider Config (expanded when enabled) */}
                {config.enabled && (
                  <div className="px-6 pb-5 pt-2 border-t border-ink-800/50 space-y-4 animate-in fade-in slide-in-from-top-2 duration-300">
                    {/* API Key (for cloud providers) */}
                    {!isLocal && !meta.noApiKey && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Key size={12} />
                          {t('aiSettings.apiKey')}
                        </label>
                        <div className="relative">
                          <input
                            type={showApiKeys[providerKey] ? 'text' : 'password'}
                            value={config.api_key || ''}
                            onChange={(e) => updateProviderField(providerKey, 'api_key', e.target.value)}
                            placeholder={
                              config.api_key_set
                                ? t('aiSettings.apiKeyPlaceholderSet', {
                                    hint: config.api_key_hint || '****',
                                  })
                                : t('aiSettings.apiKeyPlaceholder', { name: meta.name })
                            }
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
                        {config.api_key_set && !config.api_key && (
                          <p className="text-[11px] text-ink-500">
                            {t('aiSettings.apiKeyConfigured', {
                              hint: config.api_key_hint || '****',
                            })}
                            {(config.api_key_count ?? 1) > 1 &&
                              t('aiSettings.apiKeyCount', { n: config.api_key_count })}
                            {t('aiSettings.apiKeyReplace')}
                          </p>
                        )}
                      </div>
                    )}

                    {/* App ID (for volcengine) */}
                    {(meta as Record<string, unknown>).appIdField && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Key size={12} />
                          {t('aiSettings.appId')}
                        </label>
                        <input
                          type="text"
                          value={config.app_id || ''}
                          onChange={(e) => updateProviderField(providerKey, 'app_id' as keyof AIProviderConfig, e.target.value)}
                          placeholder={t('aiSettings.appIdPlaceholder')}
                          className="w-full bg-ink-950 border border-ink-800 rounded-lg px-4 py-2.5 text-sm text-ink-200 placeholder-ink-600 font-mono focus:outline-none focus:border-indigo-500 transition-colors"
                        />
                      </div>
                    )}

                    {/* Server URL (for local providers) */}
                    {isLocal && (
                      <div className="space-y-1.5">
                        <label className="text-xs font-medium text-ink-400 flex items-center gap-1.5">
                          <Server size={12} />
                          {t('aiSettings.serverUrl')}
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

                    {/* Generic model whitelist — one shape for every
                        provider, OpenAI included (its bespoke selectors
                        retired 2026-08-25). Chips = ``enabled_models``;
                        Test Connection populates ``config.models``. */}
                    {(
                      <EnabledModelsField
                        providerKey={providerKey}
                        enabledModels={config.enabled_models ?? (config.selected_model ? [config.selected_model] : [])}
                        catalog={config.models ?? meta.models}
                        onAdd={(model) => addEnabledModel(providerKey, model)}
                        onRemove={(model) => removeEnabledModel(providerKey, model)}
                        selectedModel={config.selected_model}
                        nonChatWarning={nonChatWarning}
                      />
                    )}

                    {meta.noteKey && (
                      <p
                        data-testid={`provider-note-${providerKey}`}
                        className="text-[11px] text-ink-400"
                      >
                        {t(meta.noteKey, {
                          model: (config.enabled_models ?? [])[0] || config.selected_model || '—',
                        })}
                      </p>
                    )}

                    {/* Test Connection button. A stored key is tested
                        SERVER-SIDE (2026-08-26): a blank api_key in the
                        request means "use what the server already holds",
                        so a saved provider is always testable — the old
                        re-type-to-test dead-end is gone. */}
                    {(isLocal || meta.noApiKey || config.api_key || config.api_key_set) && (
                      <div className="pt-1">
                        <button
                          data-testid={`test-connection-${providerKey}`}
                          onClick={() => testConnection(providerKey)}
                          disabled={connStatus === 'testing'}
                          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all flex items-center gap-2 border disabled:opacity-50 disabled:cursor-not-allowed ${
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
                            ? t('aiSettings.testing')
                            : connStatus === 'success'
                            ? t('aiSettings.connected')
                            : connStatus === 'error'
                            ? t('aiSettings.retryConnection')
                            : t('aiSettings.testConnection')}
                        </button>
                        {connStatus === 'error' && connectionError[providerKey] && (
                          <p className="mt-2 text-xs text-red-400">{connectionError[providerKey]}</p>
                        )}
                        {connStatus === 'success' && config.models && config.models.length > 0 && (
                          <p className="mt-2 text-xs text-green-400/70">
                            {config.models.length === 1
                              ? t('aiSettings.detectedModelOne')
                              : t('aiSettings.detectedModels', { n: config.models.length })}
                          </p>
                        )}
                        {connStatus === 'success' && quotaInfo[providerKey] && (
                          <p className="mt-1 text-xs text-ink-400">
                            {t('aiSettings.dailyQuota')}
                            {quotaInfo[providerKey]?.requests_remaining != null &&
                              t('aiSettings.quotaAccount', {
                                remaining: quotaInfo[providerKey]?.requests_remaining,
                                limit: quotaInfo[providerKey]?.requests_limit ?? '?',
                              })}
                            {quotaInfo[providerKey]?.model_requests_remaining != null &&
                              t('aiSettings.quotaModel', {
                                remaining: quotaInfo[providerKey]?.model_requests_remaining,
                                limit: quotaInfo[providerKey]?.model_requests_limit ?? '?',
                              })}
                          </p>
                        )}
                        {/* Persisted across reloads — restores when the DB has a
                            prior test result even before this session tests. */}
                        {lastTested[providerKey] && (
                          <p className="mt-1.5 text-[11px] text-ink-500">
                            {t('aiSettings.lastTested', { ago: relativeTime(lastTested[providerKey]) })}
                          </p>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {governance.nous_enabled && (
            <div
              className={`bg-ink-950 border rounded-xl overflow-hidden transition-all ${
                nousUserEnabled ? 'border-[var(--accent-border)]' : 'border-ink-800'
              }`}
            >
              {/* Header — master toggle mirrors the Ollama / LM Studio cards. */}
              <div className="px-6 py-4 flex items-center gap-4">
                <div className="p-2 rounded-lg bg-[var(--accent-soft)] text-[var(--accent-text)]">
                  <Sparkles size={18} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink-100">{t('aiSettings.nousPlatform')}</span>
                    <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-[var(--accent-soft)] text-[var(--accent-text)]">
                      {t('aiSettings.platformManaged')}
                    </span>
                  </div>
                  <p className="text-xs text-ink-400 mt-0.5">
                    {t('aiSettings.nousDesc')}
                  </p>
                </div>
                {renderToggle(nousUserEnabled, toggleNousMaster)}
              </div>

              {/* Body — collapses to header-only when the master toggle is off,
                  and each row carries a per-model toggle (blacklist semantics). */}
              {nousUserEnabled && (
                <div className="px-6 pb-4 space-y-1.5 animate-in fade-in slide-in-from-top-2 duration-300">
                  {nousModels.length === 0 ? (
                    <p className="text-xs text-ink-500">{t('aiSettings.noPlatformModels')}</p>
                  ) : (
                    [...nousModels]
                      .sort(
                        (a, b) =>
                          (NOUS_TYPE_ORDER[a.type] ?? 99) - (NOUS_TYPE_ORDER[b.type] ?? 99),
                      )
                      .map((m) => {
                        const modelEnabled = !nousDisabledModels.includes(m.name);
                        return (
                          <div
                            key={m.name}
                            className="flex items-center justify-between gap-3 text-sm text-ink-200 border-t border-ink-800 pt-1.5 first:border-t-0 first:pt-0"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span
                                className={`font-medium truncate ${
                                  modelEnabled ? 'text-ink-200' : 'text-ink-500'
                                }`}
                              >
                                {m.display_name}
                              </span>
                              <span className="shrink-0 text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-ink-800 text-ink-400">
                                {m.type}
                              </span>
                              {nousHealthWarning(m.name) && (
                                <span
                                  data-testid="model-health-badge"
                                  className="shrink-0 flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border border-warn-line bg-warn-soft text-warn"
                                >
                                  <AlertTriangle size={10} />
                                  {nousHealthWarning(m.name)}
                                </span>
                              )}
                            </div>
                            {renderToggle(modelEnabled, () => toggleNousModel(m.name))}
                          </div>
                        );
                      })
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
      )}

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
          {saveSuccess ? t('aiSettings.settingsSaved') : t('aiSettings.saveSettings')}
        </button>
      </div>

      {/* MCP moved to its own settings tab (2026-08-26 tab split). */}

      {/* G1-UI: Pending approvals — auto-hides when empty */}
      <section className="mt-8 bg-ink-900/40 border border-ink-800 rounded-lg overflow-hidden">
        <ApprovalsPanel hideWhenEmpty />
      </section>

      {/* Memory panels moved to the Memory settings tab (2026-08-26). */}


      {/* Canvas + AI Phase 2 closer: nous-center protocol probe */}
      <NousCenterVerifyPanel />
    </div>
  );
};

export default AISettings;
