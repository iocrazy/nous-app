
import React, { useState, useEffect, useCallback } from 'react';
import { UserSettings, ApiKey, AISettings as AISettingsType } from '../types';
import AISettings from './AISettings';
import {
  Save, Key, Plus, Trash2, Copy, Calendar, Shield, X, CheckSquare, Square, Edit2,
  CheckCircle, Power, Zap, Check, Loader2, AlertCircle
} from 'lucide-react';
import { LogsPanel } from './LogsPanel';
import { SystemMonitorPanel } from './SystemMonitorPanel';
import { TasksPanel } from './TasksPanel';
import { TagsSettings } from './TagsSettings';
import { ApiDocsPanel } from './ApiDocsPanel';
import { CookiesSettings } from './CookiesSettings';
import * as apiKeyService from '../services/apiKeyService';

interface SettingsViewProps {
  settings: UserSettings;
  onUpdateSettings: (s: UserSettings) => void;
  activeTab: 'general' | 'api' | 'logs' | 'monitor' | 'tasks' | 'tags' | 'ai' | 'docs' | 'cookies';
  aiSettings?: AISettingsType;
  onSaveAISettings?: (settings: AISettingsType) => void;
  /** When true, hides the outer wrapper/header for embedding in a modal */
  embedded?: boolean;
}

// Default scopes (will be overwritten by backend scopes if available)
const DEFAULT_SCOPES = [
  'videos:fetch',
  'videos:fetch:batch',
  'videos:read',
  'videos:write',
  'videos:delete',
  'videos:statistics:read',
  'tags:read',
  'tags:write',
  'tags:delete',
  'collections:read',
  'collections:write',
  'collections:delete',
  'search:read',
  'tasks:read',
  'system:read',
];

export const SettingsView: React.FC<SettingsViewProps> = ({ settings, onUpdateSettings, activeTab, aiSettings, onSaveAISettings, embedded = false }) => {
  const [localSettings, setLocalSettings] = useState<UserSettings>(settings);
  const [isKeyModalOpen, setIsKeyModalOpen] = useState(false);
  const [editingKeyId, setEditingKeyId] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);

  // Animated progress for style preview
  const [animatedProgress, setAnimatedProgress] = useState(0);

  // API Keys State
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([]);
  const [apiKeysLoading, setApiKeysLoading] = useState(false);
  const [apiKeysError, setApiKeysError] = useState<string | null>(null);

  // Available scopes from backend
  const [availableScopes, setAvailableScopes] = useState<apiKeyService.ApiKeyScopeInfo[]>([]);

  // Newly created key secret
  const [newKeySecret, setNewKeySecret] = useState<string | null>(null);

  // Form State for New/Edit Key
  const [keyForm, setKeyForm] = useState({
    name: '',
    description: '',
    expirationType: 'never' as 'never' | 'date',
    expirationDate: '',
    scopes: [] as string[],
    rateLimit: '' as string
  });
  const [formLoading, setFormLoading] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  // Load API keys from backend
  const loadApiKeys = useCallback(async () => {
    if (activeTab !== 'api') return;

    setApiKeysLoading(true);
    setApiKeysError(null);

    try {
      const keys = await apiKeyService.listApiKeys();
      setApiKeys(keys.map(k => ({
        id: k.id,
        key_id: k.key_id,
        key_prefix: k.key_prefix,
        key_value: k.key_value || undefined,
        name: k.name,
        description: k.description || undefined,
        status: k.status as 'active' | 'revoked',
        created_at: k.created_at,
        updated_at: k.updated_at,
        expires_at: k.expires_at,
        last_used_at: k.last_used_at,
        usage_count: k.usage_count,
        rate_limit: k.rate_limit,
        scopes: k.scopes,
      })));
    } catch (err) {
      console.error('Failed to load API keys:', err);
      setApiKeysError(err instanceof Error ? err.message : 'Failed to load API keys');
    } finally {
      setApiKeysLoading(false);
    }
  }, [activeTab]);

  // Load available scopes from backend
  const loadScopes = useCallback(async () => {
    try {
      const scopes = await apiKeyService.getAvailableScopes();
      setAvailableScopes(scopes);
    } catch (err) {
      console.error('Failed to load scopes:', err);
      // Use default scopes as fallback
    }
  }, []);

  // Load data when API tab is active
  useEffect(() => {
    if (activeTab === 'api') {
      loadApiKeys();
      loadScopes();
    }
  }, [activeTab, loadApiKeys, loadScopes]);

  // Animation effect for progress preview
  useEffect(() => {
    if (activeTab !== 'general') return;

    const interval = setInterval(() => {
      setAnimatedProgress(prev => {
        if (prev >= 100) return 0;
        return prev + 2;
      });
    }, 50);

    return () => clearInterval(interval);
  }, [activeTab]);

  // Lock body scroll when modal is open
  useEffect(() => {
    if (isKeyModalOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = '';
    }
    return () => {
      document.body.style.overflow = '';
    };
  }, [isKeyModalOpen]);

  const handleSaveSettings = () => {
    onUpdateSettings(localSettings);
  };

  const handleCopyKey = (key: string, id: string) => {
    navigator.clipboard.writeText(key);
    setCopiedKeyId(id);
    setTimeout(() => setCopiedKeyId(null), 2000);
  };

  const openCreateModal = () => {
    setEditingKeyId(null);
    setFormError(null);
    setNewKeySecret(null);
    setKeyForm({
      name: '',
      description: '',
      expirationType: 'never',
      expirationDate: '',
      scopes: [],
      rateLimit: ''
    });
    setIsKeyModalOpen(true);
  };

  const openEditModal = (key: ApiKey) => {
    setEditingKeyId(key.key_id);
    setFormError(null);
    setNewKeySecret(null);
    setKeyForm({
      name: key.name,
      description: key.description || '',
      expirationType: key.expires_at ? 'date' : 'never',
      expirationDate: key.expires_at ? key.expires_at.split('T')[0] : '',
      scopes: key.scopes,
      rateLimit: key.rate_limit?.toString() || ''
    });
    setIsKeyModalOpen(true);
  };

  const handleSaveKey = async () => {
    if (!keyForm.name || keyForm.scopes.length === 0) return;

    setFormLoading(true);
    setFormError(null);

    try {
      if (editingKeyId) {
        // Update existing key
        await apiKeyService.updateApiKey(editingKeyId, {
          name: keyForm.name,
          description: keyForm.description || undefined,
          scopes: keyForm.scopes,
        });
        setIsKeyModalOpen(false);
        await loadApiKeys();
      } else {
        // Create new key
        const result = await apiKeyService.createApiKey({
          name: keyForm.name,
          description: keyForm.description || undefined,
          scopes: keyForm.scopes,
          expires_at: keyForm.expirationType === 'date' && keyForm.expirationDate
            ? new Date(keyForm.expirationDate).toISOString()
            : undefined,
          rate_limit: keyForm.rateLimit ? parseInt(keyForm.rateLimit) : undefined,
        });

        // Show the key in success dialog
        setNewKeySecret(result.secret_key);
        await loadApiKeys();
      }
    } catch (err) {
      console.error('Failed to save API key:', err);
      setFormError(err instanceof Error ? err.message : 'Failed to save API key');
    } finally {
      setFormLoading(false);
    }
  };

  const toggleKeyStatus = async (keyId: string, currentStatus: string) => {
    try {
      if (currentStatus === 'active') {
        await apiKeyService.revokeApiKey(keyId);
      } else {
        await apiKeyService.updateApiKey(keyId, { status: 'active' });
      }
      await loadApiKeys();
    } catch (err) {
      console.error('Failed to toggle key status:', err);
      setApiKeysError(err instanceof Error ? err.message : 'Failed to update key status');
    }
  };

  const handleDeleteKey = async (keyId: string) => {
    if (!confirm('Are you sure you want to delete this API Key?')) return;

    try {
      await apiKeyService.deleteApiKey(keyId);
      await loadApiKeys();
    } catch (err) {
      console.error('Failed to delete API key:', err);
      setApiKeysError(err instanceof Error ? err.message : 'Failed to delete API key');
    }
  };

  const closeKeyModal = () => {
    setIsKeyModalOpen(false);
    setNewKeySecret(null);
    setFormError(null);
  };

  const toggleScope = (scope: string) => {
    setKeyForm(prev => {
      const isSelected = prev.scopes.includes(scope);
      return {
        ...prev,
        scopes: isSelected 
          ? prev.scopes.filter(s => s !== scope)
          : [...prev.scopes, scope]
      };
    });
  };

  const calculateDaysLeft = (dateStr: string) => {
    if (!dateStr) return null;
    const target = new Date(dateStr);
    const now = new Date();
    const diff = target.getTime() - now.getTime();
    const days = Math.ceil(diff / (1000 * 3600 * 24));
    return days;
  };

  return (
    <div className={embedded ? 'space-y-6' : 'max-w-5xl mx-auto space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-500'}>

      {/* Header - hidden in embedded mode */}
      {!embedded && (
      <div>
         <h1 className="text-2xl font-bold text-white mb-2">Settings</h1>
         <p className="text-zinc-400">Manage your application preferences and API access credentials.</p>
      </div>
      )}

      {/* General Settings Tab */}
      {activeTab === 'general' && (
        <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
           
           {/* Section 1: Download Progress Style */}
           <div className="px-6 py-4 border-y border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
              <div className="p-2 bg-purple-500/10 rounded-lg text-purple-400">
                 <Zap size={20} />
              </div>
              <h2 className="font-semibold text-zinc-200">Download Progress Style</h2>
           </div>
           <div className="p-6">
              <div className="grid grid-cols-2 gap-4">
                 {/* Neon Style */}
                 <label
                    className={`relative cursor-pointer rounded-xl border-2 p-4 transition-all ${
                       localSettings.progressStyle === 'neon' || !localSettings.progressStyle
                          ? 'border-purple-500 bg-purple-500/10'
                          : 'border-zinc-800 hover:border-zinc-700'
                    }`}
                    onClick={() => setLocalSettings({...localSettings, progressStyle: 'neon'})}
                 >
                    <div className="flex flex-col items-center gap-3">
                       <div className="w-full aspect-video rounded-lg bg-zinc-950 border border-purple-500/50 flex items-center justify-center relative overflow-hidden">
                          {/* Neon border preview - animated only when selected */}
                          <svg className="absolute inset-0 w-full h-full" style={{
                             filter: (localSettings.progressStyle === 'neon' || !localSettings.progressStyle)
                                ? 'drop-shadow(0 0 6px rgba(139, 92, 246, 0.6))'
                                : undefined
                          }}>
                             <rect x="8" y="8" width="calc(100% - 16px)" height="calc(100% - 16px)" rx="6" fill="none" stroke="rgba(139, 92, 246, 0.2)" strokeWidth="2" />
                             <rect x="8" y="8" width="calc(100% - 16px)" height="calc(100% - 16px)" rx="6" fill="none" stroke="url(#neonGradientPreview)" strokeWidth="3"
                                strokeDasharray={`${(localSettings.progressStyle === 'neon' || !localSettings.progressStyle) ? animatedProgress * 3.6 : 50 * 3.6} 360`}
                                strokeLinecap="round" className="transition-all duration-100" />
                             <defs>
                                <linearGradient id="neonGradientPreview" x1="0%" y1="0%" x2="100%" y2="100%">
                                   <stop offset="0%" stopColor="#a855f7" />
                                   <stop offset="50%" stopColor="#6366f1" />
                                   <stop offset="100%" stopColor="#a855f7" />
                                </linearGradient>
                             </defs>
                          </svg>
                          <span className="text-2xl font-bold text-white z-10">
                             {(localSettings.progressStyle === 'neon' || !localSettings.progressStyle) ? `${animatedProgress}%` : '50%'}
                          </span>
                       </div>
                       <div className="text-center">
                          <div className="font-medium text-zinc-200">Neon Border</div>
                          <div className="text-xs text-zinc-500">Glowing border animation</div>
                       </div>
                    </div>
                    {(localSettings.progressStyle === 'neon' || !localSettings.progressStyle) && (
                       <div className="absolute top-2 right-2 w-5 h-5 bg-purple-500 rounded-full flex items-center justify-center">
                          <Check size={12} className="text-white" />
                       </div>
                    )}
                 </label>

                 {/* Wave Style */}
                 <label
                    className={`relative cursor-pointer rounded-xl border-2 p-4 transition-all ${
                       localSettings.progressStyle === 'wave'
                          ? 'border-purple-500 bg-purple-500/10'
                          : 'border-zinc-800 hover:border-zinc-700'
                    }`}
                    onClick={() => setLocalSettings({...localSettings, progressStyle: 'wave'})}
                 >
                    <div className="flex flex-col items-center gap-3">
                       <div className="w-full aspect-video rounded-lg bg-zinc-950 border border-indigo-500/50 flex items-center justify-center relative overflow-hidden">
                          {/* Wave preview - animated only when selected */}
                          <div
                             className="absolute inset-x-0 bottom-0 transition-all duration-300 ease-out"
                             style={{ height: localSettings.progressStyle === 'wave' ? `${animatedProgress}%` : '50%' }}
                          >
                             {/* Wave layer 1 - back wave with gradient fill */}
                             <svg
                                className="absolute -top-5 left-0 w-[200%] h-[calc(100%+20px)]"
                                style={{
                                   animation: localSettings.progressStyle === 'wave' ? 'wavePreview 3s ease-in-out infinite' : 'none',
                                   animationDelay: '-1s'
                                }}
                                viewBox="0 0 1200 200"
                                preserveAspectRatio="none"
                             >
                                <defs>
                                   <linearGradient id="waveGrad1" x1="0%" y1="0%" x2="0%" y2="100%">
                                      <stop offset="0%" stopColor="#7c3aed" />
                                      <stop offset="100%" stopColor="#4f46e5" />
                                   </linearGradient>
                                </defs>
                                <path d="M0,25 C150,50 250,0 400,25 C550,50 650,0 800,25 C950,50 1050,0 1200,25 L1200,200 L0,200 Z" fill="url(#waveGrad1)" />
                             </svg>
                             {/* Wave layer 2 - middle wave */}
                             <svg
                                className="absolute -top-4 left-0 w-[200%] h-[calc(100%+16px)]"
                                style={{
                                   animation: localSettings.progressStyle === 'wave' ? 'wavePreview 2.2s ease-in-out infinite reverse' : 'none',
                                   animationDelay: '-0.5s'
                                }}
                                viewBox="0 0 1200 200"
                                preserveAspectRatio="none"
                             >
                                <defs>
                                   <linearGradient id="waveGrad2" x1="0%" y1="0%" x2="0%" y2="100%">
                                      <stop offset="0%" stopColor="#8b5cf6" />
                                      <stop offset="100%" stopColor="#6366f1" />
                                   </linearGradient>
                                </defs>
                                <path d="M0,20 C100,45 200,0 300,25 C400,50 500,0 600,25 C700,50 800,0 900,25 C1000,50 1100,0 1200,20 L1200,200 L0,200 Z" fill="url(#waveGrad2)" />
                             </svg>
                             {/* Wave layer 3 - front wave (most visible) */}
                             <svg
                                className="absolute -top-6 left-0 w-[200%] h-[calc(100%+24px)]"
                                style={{ animation: localSettings.progressStyle === 'wave' ? 'wavePreview 3s ease-in-out infinite' : 'none' }}
                                viewBox="0 0 1200 200"
                                preserveAspectRatio="none"
                             >
                                <defs>
                                   <linearGradient id="waveGrad3" x1="0%" y1="0%" x2="0%" y2="100%">
                                      <stop offset="0%" stopColor="#a78bfa" />
                                      <stop offset="100%" stopColor="#818cf8" />
                                   </linearGradient>
                                </defs>
                                <path d="M0,15 C80,40 160,0 240,20 C320,45 400,0 480,20 C560,45 640,0 720,20 C800,45 880,0 960,20 C1040,45 1120,0 1200,15 L1200,200 L0,200 Z" fill="url(#waveGrad3)" />
                             </svg>
                          </div>
                          <span className="text-2xl font-bold text-white z-10 drop-shadow-lg">
                             {localSettings.progressStyle === 'wave' ? `${animatedProgress}%` : '50%'}
                          </span>
                       </div>
                       <div className="text-center">
                          <div className="font-medium text-zinc-200">Wave Liquid</div>
                          <div className="text-xs text-zinc-500">Rising wave animation</div>
                       </div>
                    </div>
                    {localSettings.progressStyle === 'wave' && (
                       <div className="absolute top-2 right-2 w-5 h-5 bg-purple-500 rounded-full flex items-center justify-center">
                          <Check size={12} className="text-white" />
                       </div>
                    )}
                 </label>
              </div>
           </div>

           {/* Actions Footer */}
           <div className="px-6 py-4 border-t border-zinc-800 bg-zinc-950/50 flex justify-end">
              <button
                  onClick={handleSaveSettings}
                  className="bg-indigo-600 hover:bg-indigo-500 text-white px-6 py-2.5 rounded-lg font-medium transition-colors flex items-center gap-2 shadow-lg shadow-indigo-900/20"
              >
                  <Save size={18} />
                  Save Changes
              </button>
           </div>
        </section>
      )}

      {/* API Management Tab */}
      {activeTab === 'api' && (
        <>
        <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
           <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50 flex items-center justify-between">
              <div className="flex items-center gap-3">
                 <div className="p-2 bg-pink-500/10 rounded-lg text-pink-400">
                    <Key size={20} />
                 </div>
                 <h2 className="font-semibold text-zinc-200">API Access Management</h2>
              </div>
              <button
                 onClick={openCreateModal}
                 className="bg-zinc-100 hover:bg-white text-zinc-900 px-4 py-2 rounded-lg text-sm font-semibold transition-colors flex items-center gap-2"
              >
                 <Plus size={16} />
                 Create API Key
              </button>
           </div>

           {/* Loading/Error State */}
           {apiKeysLoading && (
              <div className="p-12 flex items-center justify-center gap-3 text-zinc-500">
                 <Loader2 size={20} className="animate-spin" />
                 <span>Loading API keys...</span>
              </div>
           )}

           {apiKeysError && (
              <div className="m-4 p-4 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3 text-red-400">
                 <AlertCircle size={20} />
                 <span>{apiKeysError}</span>
                 <button
                    onClick={loadApiKeys}
                    className="ml-auto text-xs underline hover:no-underline"
                 >
                    Retry
                 </button>
              </div>
           )}

           {!apiKeysLoading && !apiKeysError && (
           <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-zinc-400">
                 <thead className="bg-zinc-950/50 text-zinc-500 border-b border-zinc-800 uppercase text-xs">
                    <tr>
                       <th className="px-6 py-4 font-medium">Name</th>
                       <th className="px-6 py-4 font-medium">Key Prefix</th>
                       <th className="px-6 py-4 font-medium">Status</th>
                       <th className="px-6 py-4 font-medium">Created</th>
                       <th className="px-6 py-4 font-medium">Expires</th>
                       <th className="px-6 py-4 font-medium">Usage</th>
                       <th className="px-6 py-4 font-medium text-right">Actions</th>
                    </tr>
                 </thead>
                 <tbody className="divide-y divide-zinc-800/50">
                    {apiKeys.map((key) => (
                       <tr key={key.id} className="group hover:bg-zinc-800/30 transition-colors">
                          <td className="px-6 py-4">
                             <div className="flex flex-col">
                                <span className="font-medium text-zinc-300">{key.name}</span>
                                {key.description && (
                                   <span className="text-xs text-zinc-500 truncate max-w-[200px]">{key.description}</span>
                                )}
                             </div>
                          </td>
                          <td className="px-6 py-4 font-mono text-xs">
                             <div className="flex items-center gap-2">
                                <span className="opacity-70 bg-zinc-950 px-2 py-1 rounded border border-zinc-800 select-all">{key.key_prefix}</span>
                                <button
                                  onClick={() => handleCopyKey(key.key_value || key.key_prefix, key.id.toString())}
                                  className={`transition-colors flex-shrink-0 ${copiedKeyId === key.id.toString() ? 'text-green-500' : 'text-zinc-500 hover:text-indigo-400'}`}
                                  title="Copy full key"
                                >
                                   {copiedKeyId === key.id.toString() ? <CheckCircle size={14} /> : <Copy size={14} />}
                                </button>
                             </div>
                          </td>
                          <td className="px-6 py-4">
                             <button
                                onClick={() => toggleKeyStatus(key.key_id, key.status)}
                                className={`px-2.5 py-1 rounded-full text-xs font-medium border flex items-center gap-1.5 transition-all hover:opacity-80 ${
                                key.status === 'active'
                                   ? 'bg-green-500/10 text-green-400 border-green-500/20'
                                   : 'bg-red-500/10 text-red-400 border-red-500/20'
                             }`}>
                                <Power size={10} />
                                {key.status === 'active' ? 'Active' : 'Revoked'}
                             </button>
                          </td>
                          <td className="px-6 py-4 text-xs">
                             {new Date(key.created_at).toLocaleDateString()}
                          </td>
                          <td className="px-6 py-4">
                             {!key.expires_at ? (
                                <span className="text-zinc-500">Never</span>
                             ) : (
                                <div className="flex flex-col">
                                   <span className="text-xs">{new Date(key.expires_at).toLocaleDateString()}</span>
                                   <span className="text-xs text-zinc-600">{calculateDaysLeft(key.expires_at)} days left</span>
                                </div>
                             )}
                          </td>
                          <td className="px-6 py-4 text-xs">
                             <span className="text-zinc-400">{key.usage_count || 0}</span>
                             {key.last_used_at && (
                                <div className="text-zinc-600 text-xs">
                                   Last: {new Date(key.last_used_at).toLocaleDateString()}
                                </div>
                             )}
                          </td>
                          <td className="px-6 py-4 text-right">
                             <div className="flex items-center justify-end gap-2">
                                <button
                                  onClick={() => openEditModal(key)}
                                  className="p-1.5 hover:bg-zinc-800 rounded text-zinc-500 hover:text-zinc-300 transition-colors"
                                  title="Edit"
                                >
                                   <Edit2 size={14} />
                                </button>
                                <button
                                   onClick={() => handleDeleteKey(key.key_id)}
                                   className="p-1.5 hover:bg-red-900/30 rounded text-zinc-500 hover:text-red-400 transition-colors"
                                   title="Delete"
                                >
                                   <Trash2 size={14} />
                                </button>
                             </div>
                          </td>
                       </tr>
                    ))}
                    {apiKeys.length === 0 && (
                       <tr>
                          <td colSpan={7} className="px-6 py-12 text-center text-zinc-500 italic">
                             No API keys generated yet.
                          </td>
                       </tr>
                    )}
                 </tbody>
              </table>
           </div>
           )}
        </section>

        </>
      )}

      {/* Logs Tab */}
      {activeTab === 'logs' && (
        <LogsPanel />
      )}

      {/* Monitor Tab */}
      {activeTab === 'monitor' && (
        <SystemMonitorPanel />
      )}

      {/* Tasks Tab */}
      {activeTab === 'tasks' && (
        <TasksPanel />
      )}

      {/* Tags Tab */}
      {activeTab === 'tags' && (
        <TagsSettings />
      )}

      {/* AI Tab */}
      {activeTab === 'ai' && aiSettings && onSaveAISettings && (
        <AISettings settings={aiSettings} onSave={onSaveAISettings} />
      )}

      {/* Docs Tab */}
      {activeTab === 'docs' && (
        <ApiDocsPanel />
      )}

      {/* Cookies Tab */}
      {activeTab === 'cookies' && (
        <CookiesSettings />
      )}

      {/* Wave animation keyframes */}
      <style>{`
        @keyframes wavePreview {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        @keyframes shinePreview {
          0%, 100% { transform: translateX(-100%); opacity: 0; }
          50% { transform: translateX(100%); opacity: 1; }
        }
      `}</style>

      {/* Create/Edit Key Modal */}
      {isKeyModalOpen && (
         <div className="absolute inset-0 bg-black/60 backdrop-blur-sm z-20 flex items-start justify-center pt-12 p-4 overflow-y-auto">
            <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[80vh]">
               <div className="px-6 py-5 border-b border-zinc-800 flex justify-between items-center bg-zinc-950/50">
                  <h3 className="text-lg font-bold text-white">
                     {newKeySecret ? 'API Key Created!' : editingKeyId ? 'Edit API Key' : 'Create New API Key'}
                  </h3>
                  <button onClick={closeKeyModal} className="text-zinc-500 hover:text-zinc-300">
                     <X size={20} />
                  </button>
               </div>

               {/* Show Key After Creation */}
               {newKeySecret ? (
                  <div className="p-6 space-y-6">
                     <div className="p-4 bg-green-500/10 border border-green-500/20 rounded-lg">
                        <div className="flex items-start gap-3">
                           <CheckCircle size={20} className="text-green-400 mt-0.5 flex-shrink-0" />
                           <div className="text-sm text-green-300">
                              <p className="font-medium">API key created successfully!</p>
                              <p className="text-green-300/70 mt-1">You can always copy this key from the table using the copy button.</p>
                           </div>
                        </div>
                     </div>

                     <div className="space-y-2">
                        <label className="text-sm font-medium text-zinc-300">Your API Key</label>
                        <div className="flex items-center gap-2">
                           <div className="flex-1 relative">
                              <input
                                 type="text"
                                 value={newKeySecret}
                                 readOnly
                                 className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 font-mono text-sm pr-12"
                              />
                              <button
                                 onClick={() => handleCopyKey(newKeySecret, 'new-key')}
                                 className={`absolute right-3 top-1/2 -translate-y-1/2 ${copiedKeyId === 'new-key' ? 'text-green-500' : 'text-zinc-500 hover:text-indigo-400'}`}
                              >
                                 {copiedKeyId === 'new-key' ? <CheckCircle size={18} /> : <Copy size={18} />}
                              </button>
                           </div>
                        </div>
                        {copiedKeyId === 'new-key' && (
                           <p className="text-xs text-green-500 animate-in fade-in duration-200">Copied to clipboard!</p>
                        )}
                     </div>

                     <div className="pt-4 border-t border-zinc-800">
                        <button
                           onClick={closeKeyModal}
                           className="w-full px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors font-medium text-sm"
                        >
                           Done
                        </button>
                     </div>
                  </div>
               ) : (
                  <>
               <div className="p-6 overflow-y-auto space-y-6">
                  {formError && (
                     <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-lg flex items-center gap-3 text-red-400 text-sm">
                        <AlertCircle size={18} />
                        <span>{formError}</span>
                     </div>
                  )}

                  <div className="space-y-2">
                     <label className="text-sm font-medium text-zinc-300">Key Name</label>
                     <input
                        type="text"
                        placeholder="e.g. Production Web Client"
                        value={keyForm.name}
                        onChange={(e) => setKeyForm({...keyForm, name: e.target.value})}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 outline-none focus:border-indigo-500 transition-colors"
                     />
                  </div>

                  <div className="space-y-2">
                     <label className="text-sm font-medium text-zinc-300">Description (Optional)</label>
                     <input
                        type="text"
                        placeholder="e.g. Used for automated scripts"
                        value={keyForm.description}
                        onChange={(e) => setKeyForm({...keyForm, description: e.target.value})}
                        className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 outline-none focus:border-indigo-500 transition-colors"
                     />
                  </div>

                  {!editingKeyId && (
                  <div className="space-y-2">
                     <label className="text-sm font-medium text-zinc-300">Expiration</label>
                     <div className="flex gap-4 mb-2">
                        <label className="flex items-center gap-2 cursor-pointer text-sm text-zinc-400 hover:text-zinc-200">
                           <input
                              type="radio"
                              name="expirationType"
                              checked={keyForm.expirationType === 'never'}
                              onChange={() => setKeyForm({...keyForm, expirationType: 'never'})}
                              className="accent-indigo-500"
                           />
                           Never Expires
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer text-sm text-zinc-400 hover:text-zinc-200">
                           <input
                              type="radio"
                              name="expirationType"
                              checked={keyForm.expirationType === 'date'}
                              onChange={() => setKeyForm({...keyForm, expirationType: 'date'})}
                              className="accent-indigo-500"
                           />
                           Select Date
                        </label>
                     </div>

                     {keyForm.expirationType === 'date' && (
                       <div className="relative animate-in fade-in slide-in-from-top-2">
                          <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 w-4 h-4" />
                          <input
                             type="date"
                             value={keyForm.expirationDate}
                             min={new Date().toISOString().split('T')[0]}
                             onChange={(e) => setKeyForm({...keyForm, expirationDate: e.target.value})}
                             className="w-full bg-zinc-950 border border-zinc-800 rounded-lg pl-10 pr-4 py-3 text-zinc-200 outline-none focus:border-indigo-500 transition-colors"
                          />
                          {keyForm.expirationDate && (
                             <div className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-indigo-400 font-medium">
                                Expires in {calculateDaysLeft(keyForm.expirationDate)} days
                             </div>
                          )}
                       </div>
                     )}
                  </div>
                  )}

                  <div className="space-y-3">
                     <div className="flex items-center justify-between">
                        <label className="text-sm font-medium text-zinc-300 flex items-center gap-2">
                           <Shield size={14} className="text-indigo-400"/>
                           API Scopes (Required)
                        </label>
                        <button
                           onClick={() => {
                              const allScopes = availableScopes.length > 0
                                 ? availableScopes.map(s => s.scope)
                                 : DEFAULT_SCOPES;
                              setKeyForm({...keyForm, scopes: allScopes});
                           }}
                           className="text-xs text-indigo-400 hover:text-indigo-300"
                        >
                           Select All
                        </button>
                     </div>
                     <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-48 overflow-y-auto border border-zinc-800 rounded-lg p-3 bg-zinc-950/30">
                        {(availableScopes.length > 0 ? availableScopes : DEFAULT_SCOPES.map(s => ({ scope: s, name: s, description: '', category: '' }))).map((scopeInfo) => {
                           const scope = typeof scopeInfo === 'string' ? scopeInfo : scopeInfo.scope;
                           const name = typeof scopeInfo === 'string' ? scopeInfo : scopeInfo.name;
                           const desc = typeof scopeInfo === 'string' ? '' : scopeInfo.description;
                           return (
                           <label
                              key={scope}
                              onClick={(e) => {
                                 e.preventDefault();
                                 toggleScope(scope);
                              }}
                              className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
                                 keyForm.scopes.includes(scope)
                                    ? 'bg-indigo-500/10 border-indigo-500/50 text-indigo-300'
                                    : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:border-zinc-700'
                              }`}
                           >
                              <div className={`w-4 h-4 rounded flex items-center justify-center border flex-shrink-0 ${
                                 keyForm.scopes.includes(scope) ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600'
                              }`}>
                                 {keyForm.scopes.includes(scope) && <Check size={10} className="text-white" />}
                              </div>
                              <div className="min-w-0 flex-1">
                                 <span className="text-xs font-medium block truncate" title={scope}>{name}</span>
                                 {desc && <span className="text-xs text-zinc-500 block truncate">{desc}</span>}
                              </div>
                           </label>
                        )})}
                     </div>
                  </div>
               </div>

               <div className="px-6 py-5 border-t border-zinc-800 bg-zinc-950/50 flex justify-end gap-3">
                  <button
                     onClick={closeKeyModal}
                     className="px-5 py-2.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors font-medium text-sm"
                     disabled={formLoading}
                  >
                     Cancel
                  </button>
                  <button
                     onClick={handleSaveKey}
                     disabled={formLoading || !keyForm.name || keyForm.scopes.length === 0 || (keyForm.expirationType === 'date' && !keyForm.expirationDate)}
                     className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-indigo-900/20 flex items-center gap-2"
                  >
                     {formLoading && <Loader2 size={16} className="animate-spin" />}
                     {editingKeyId ? 'Save Changes' : 'Create Key'}
                  </button>
               </div>
                  </>
               )}
            </div>
         </div>
      )}
    </div>
  );
};
