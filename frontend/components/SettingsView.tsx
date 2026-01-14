
import React, { useState } from 'react';
import { UserSettings, ApiKey } from '../types';
import { 
  Save, FolderOpen, Key, Plus, Trash2, Copy, Calendar, Shield, X, CheckSquare, Square, Edit2,
  Clock, CheckCircle, Power, Database
} from 'lucide-react';

interface SettingsViewProps {
  settings: UserSettings;
  onUpdateSettings: (s: UserSettings) => void;
  activeTab: 'general' | 'api';
}

const API_SCOPES = [
  '/api/v1/douyin/fetch',
  '/api/v1/douyin/fetch/batch',
  '/api/v1/douyin/videos',
  '/api/v1/douyin/videos/{id}',
  '/api/v1/douyin/statistics',
  '/api/v1/douyin/retry/{id}',
  '/api/v1/auth/*',
];

export const SettingsView: React.FC<SettingsViewProps> = ({ settings, onUpdateSettings, activeTab }) => {
  const [localSettings, setLocalSettings] = useState<UserSettings>(settings);
  const [isKeyModalOpen, setIsKeyModalOpen] = useState(false);
  const [editingKeyId, setEditingKeyId] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);
  
  // Mock API Keys State
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([
    {
      id: '1',
      name: 'heygo',
      key: '2fk0czxa5FMllBkD12y0k3oZyzQnsYDfqRZ442e/uzNN8mIyTacq4EC2EA==',
      status: 'active',
      created_at: '2025-06-19',
      expires_at: 'Never',
      scopes: ['/api/v1/douyin/fetch', '/api/v1/douyin/videos']
    }
  ]);

  // Form State for New/Edit Key
  const [keyForm, setKeyForm] = useState({
    name: '',
    expirationType: 'never' as 'never' | 'date',
    expirationDate: '',
    scopes: [] as string[]
  });

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
    setKeyForm({
      name: '',
      expirationType: 'never',
      expirationDate: '',
      scopes: []
    });
    setIsKeyModalOpen(true);
  };

  const openEditModal = (key: ApiKey) => {
    setEditingKeyId(key.id);
    setKeyForm({
      name: key.name,
      expirationType: key.expires_at === 'Never' ? 'never' : 'date',
      expirationDate: key.expires_at === 'Never' ? '' : key.expires_at,
      scopes: key.scopes
    });
    setIsKeyModalOpen(true);
  };

  const handleSaveKey = () => {
    if (!keyForm.name) return;
    
    const finalExpiration = keyForm.expirationType === 'never' ? 'Never' : keyForm.expirationDate;

    if (editingKeyId) {
      // Update existing
      setApiKeys(prev => prev.map(k => k.id === editingKeyId ? {
        ...k,
        name: keyForm.name,
        expires_at: finalExpiration,
        scopes: keyForm.scopes
      } : k));
    } else {
      // Create new
      const newKey: ApiKey = {
        id: Math.random().toString(36).substr(2, 9),
        name: keyForm.name,
        key: Array(40).fill(0).map(() => Math.random().toString(36).charAt(2)).join('') + '==',
        status: 'active',
        created_at: new Date().toISOString().split('T')[0],
        expires_at: finalExpiration,
        scopes: keyForm.scopes
      };
      setApiKeys([...apiKeys, newKey]);
    }

    setIsKeyModalOpen(false);
  };

  const toggleKeyStatus = (id: string) => {
    setApiKeys(prev => prev.map(k => k.id === id ? {
      ...k,
      status: k.status === 'active' ? 'inactive' : 'active'
    } : k));
  };

  const handleDeleteKey = (id: string) => {
    if (confirm('Are you sure you want to delete this API Key?')) {
      setApiKeys(apiKeys.filter(k => k.id !== id));
    }
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
    <div className="max-w-5xl mx-auto space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-500">
      
      {/* Header */}
      <div>
         <h1 className="text-2xl font-bold text-white mb-2">Settings</h1>
         <p className="text-zinc-400">Manage your application preferences and API access credentials.</p>
      </div>

      {/* General Settings Tab */}
      {activeTab === 'general' && (
        <section className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden animate-in fade-in duration-300">
           
           {/* Section 1: Server Config */}
           <div className="px-6 py-4 border-b border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
              <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400">
                 <FolderOpen size={20} />
              </div>
              <h2 className="font-semibold text-zinc-200">Server Configuration</h2>
           </div>
           <div className="p-6 space-y-6">
              <div className="space-y-2">
                  <label className="text-sm font-medium text-zinc-400">Default Download Path (Server-side)</label>
                  <input 
                      type="text" 
                      value={localSettings.downloadPath}
                      onChange={(e) => setLocalSettings({...localSettings, downloadPath: e.target.value})}
                      className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2.5 text-zinc-200 outline-none focus:border-indigo-500 transition-colors font-mono text-sm"
                  />
                  <p className="text-xs text-zinc-500 mt-1">
                      Videos will be saved to this directory on the host machine running the downloader service.
                  </p>
              </div>
           </div>

           {/* Section 2: Database Config */}
           <div className="px-6 py-4 border-y border-zinc-800 bg-zinc-900/50 flex items-center gap-3">
              <div className="p-2 bg-emerald-500/10 rounded-lg text-emerald-400">
                 <Database size={20} />
              </div>
              <h2 className="font-semibold text-zinc-200">Database Configuration</h2>
           </div>
           <div className="p-6 space-y-6">
              <div className="space-y-4">
                  <div className="space-y-2">
                      <label className="text-sm font-medium text-zinc-400">Supabase URL</label>
                      <input 
                          type="text" 
                          placeholder="https://your-project.supabase.co"
                          value={localSettings.supabaseUrl || ''}
                          onChange={(e) => setLocalSettings({...localSettings, supabaseUrl: e.target.value})}
                          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2.5 text-zinc-200 outline-none focus:border-indigo-500 transition-colors font-mono text-sm"
                      />
                  </div>
                  <div className="space-y-2">
                      <label className="text-sm font-medium text-zinc-400">Supabase Anon Key</label>
                      <input 
                          type="text" 
                          placeholder="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
                          value={localSettings.supabaseAnonKey || ''}
                          onChange={(e) => setLocalSettings({...localSettings, supabaseAnonKey: e.target.value})}
                          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2.5 text-zinc-200 outline-none focus:border-indigo-500 transition-colors font-mono text-sm"
                      />
                  </div>
                  <div className="flex items-center gap-2 p-3 bg-indigo-500/5 rounded-lg border border-indigo-500/20 text-xs text-indigo-300">
                     <Clock size={14} />
                     <span>Changing database credentials will require a page reload to re-initialize the connection.</span>
                  </div>
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

           <div className="overflow-x-auto">
              <table className="w-full text-left text-sm text-zinc-400">
                 <thead className="bg-zinc-950/50 text-zinc-500 border-b border-zinc-800 uppercase text-xs">
                    <tr>
                       <th className="px-6 py-4 font-medium">Name</th>
                       <th className="px-6 py-4 font-medium">Key Secret</th>
                       <th className="px-6 py-4 font-medium">Status</th>
                       <th className="px-6 py-4 font-medium">Created</th>
                       <th className="px-6 py-4 font-medium">Expires</th>
                       <th className="px-6 py-4 font-medium text-right">Actions</th>
                    </tr>
                 </thead>
                 <tbody className="divide-y divide-zinc-800/50">
                    {apiKeys.map((key) => (
                       <tr key={key.id} className="group hover:bg-zinc-800/30 transition-colors">
                          <td className="px-6 py-4 font-medium text-zinc-300">{key.name}</td>
                          <td className="px-6 py-4 font-mono text-xs">
                             <div className="flex items-center gap-2 max-w-[220px]">
                                <span className="truncate opacity-50 bg-zinc-950 px-2 py-1 rounded border border-zinc-800 select-all">{key.key}</span>
                                <button 
                                  onClick={() => handleCopyKey(key.key, key.id)}
                                  className={`transition-colors flex-shrink-0 ${copiedKeyId === key.id ? 'text-green-500' : 'text-zinc-500 hover:text-indigo-400'}`}
                                  title="Copy"
                                >
                                   {copiedKeyId === key.id ? <CheckCircle size={14} /> : <Copy size={14} />}
                                </button>
                                {copiedKeyId === key.id && <span className="text-xs text-green-500 animate-in fade-in duration-200">Copied!</span>}
                             </div>
                          </td>
                          <td className="px-6 py-4">
                             <button 
                                onClick={() => toggleKeyStatus(key.id)}
                                className={`px-2.5 py-1 rounded-full text-xs font-medium border flex items-center gap-1.5 transition-all hover:opacity-80 ${
                                key.status === 'active' 
                                   ? 'bg-green-500/10 text-green-400 border-green-500/20' 
                                   : 'bg-red-500/10 text-red-400 border-red-500/20'
                             }`}>
                                <Power size={10} />
                                {key.status === 'active' ? 'Active' : 'Inactive'}
                             </button>
                          </td>
                          <td className="px-6 py-4">{key.created_at}</td>
                          <td className="px-6 py-4">
                             {key.expires_at === 'Never' ? (
                                <span className="text-zinc-500">Never</span>
                             ) : (
                                <div className="flex flex-col">
                                   <span>{key.expires_at}</span>
                                   <span className="text-xs text-zinc-600">{calculateDaysLeft(key.expires_at)} days left</span>
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
                                   onClick={() => handleDeleteKey(key.id)}
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
                          <td colSpan={6} className="px-6 py-12 text-center text-zinc-500 italic">
                             No API keys generated yet.
                          </td>
                       </tr>
                    )}
                 </tbody>
              </table>
           </div>
        </section>
      )}

      {/* Create/Edit Key Modal */}
      {isKeyModalOpen && (
         <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-200 flex flex-col max-h-[90vh]">
               <div className="px-6 py-5 border-b border-zinc-800 flex justify-between items-center bg-zinc-950/50">
                  <h3 className="text-lg font-bold text-white">{editingKeyId ? 'Edit API Key' : 'Create New API Key'}</h3>
                  <button onClick={() => setIsKeyModalOpen(false)} className="text-zinc-500 hover:text-zinc-300">
                     <X size={20} />
                  </button>
               </div>
               
               <div className="p-6 overflow-y-auto space-y-6">
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

                  <div className="space-y-3">
                     <div className="flex items-center justify-between">
                        <label className="text-sm font-medium text-zinc-300 flex items-center gap-2">
                           <Shield size={14} className="text-indigo-400"/>
                           API Scopes (Required)
                        </label>
                        <button 
                           onClick={() => setKeyForm({...keyForm, scopes: API_SCOPES})}
                           className="text-xs text-indigo-400 hover:text-indigo-300"
                        >
                           Select All
                        </button>
                     </div>
                     <div className="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-48 overflow-y-auto border border-zinc-800 rounded-lg p-3 bg-zinc-950/30">
                        {API_SCOPES.map((scope) => (
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
                              <div className={`w-4 h-4 rounded flex items-center justify-center border ${
                                 keyForm.scopes.includes(scope) ? 'bg-indigo-500 border-indigo-500' : 'border-zinc-600'
                              }`}>
                                 {keyForm.scopes.includes(scope) && <CheckSquare size={12} className="text-white" />}
                              </div>
                              <span className="text-xs font-mono truncate" title={scope}>{scope}</span>
                           </label>
                        ))}
                     </div>
                  </div>
               </div>

               <div className="px-6 py-5 border-t border-zinc-800 bg-zinc-950/50 flex justify-end gap-3">
                  <button 
                     onClick={() => setIsKeyModalOpen(false)}
                     className="px-5 py-2.5 rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors font-medium text-sm"
                  >
                     Cancel
                  </button>
                  <button 
                     onClick={handleSaveKey}
                     disabled={!keyForm.name || keyForm.scopes.length === 0 || (keyForm.expirationType === 'date' && !keyForm.expirationDate)}
                     className="px-5 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white transition-colors font-medium text-sm disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-indigo-900/20"
                  >
                     {editingKeyId ? 'Save Changes' : 'Create Key'}
                  </button>
               </div>
            </div>
         </div>
      )}
    </div>
  );
};
