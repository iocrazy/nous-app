import { useState, useEffect, useCallback } from 'react';
import {
  Brain, Plus, Pencil, Trash2, Loader2,
  ToggleLeft, ToggleRight, ArrowLeft,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { getAuthHeaders } from '../../services/parserService';
import { getApiUrl } from '../../utils/apiConfig';
import { useToast } from '../../components/Toast';
import { useConfirm } from '../../components/ConfirmDialog';
import {
  NousModel, ModelFormData, EMPTY_FORM,
  CATEGORY_LABELS, PRICING_LABELS, CATEGORY_COLORS,
  NousModelFormModal,
} from './NousModelFormModal';

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function fetchModels(): Promise<NousModel[]> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/admin/nous-models`, { headers });
  if (!res.ok) throw new Error(`Failed to fetch models: ${res.status}`);
  return res.json();
}

async function createModel(data: ModelFormData): Promise<NousModel> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/admin/nous-models`, {
    method: 'POST',
    headers,
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Create failed: ${res.status}`);
  }
  return res.json();
}

async function updateModel(id: string, data: Partial<ModelFormData>): Promise<NousModel> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/admin/nous-models/${id}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Update failed: ${res.status}`);
  }
  return res.json();
}

async function deleteModel(id: string): Promise<void> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${getApiUrl()}/api/v1/admin/nous-models/${id}`, {
    method: 'DELETE',
    headers,
  });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function AdminNousModelsPage() {
  const navigate = useNavigate();
  const { addToast } = useToast();
  const confirm = useConfirm();

  const [models, setModels] = useState<readonly NousModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<NousModel | null>(null);
  const [form, setForm] = useState<ModelFormData>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const loadModels = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchModels();
      setModels(data);
    } catch (err) {
      console.error('Failed to load nous models:', err);
      addToast('Failed to load models', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

  useEffect(() => { loadModels(); }, [loadModels]);

  const handleFormChange = useCallback((patch: Partial<ModelFormData>) => {
    setForm((prev) => ({ ...prev, ...patch }));
  }, []);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setModalOpen(true);
  };

  const openEdit = (model: NousModel) => {
    setEditing(model);
    setForm({
      name: model.name,
      display_name: model.display_name,
      category: model.category,
      actual_provider: model.actual_provider,
      actual_model: model.actual_model,
      api_key: '',
      app_id: model.app_id ?? '',
      base_url: model.base_url ?? '',
      pricing_type: model.pricing_type,
      pricing_value: model.pricing_value,
      is_enabled: model.is_enabled,
      sort_order: model.sort_order,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.display_name.trim()) {
      addToast('Name and Display Name are required', 'error');
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        if (!form.api_key) {
          const { api_key: _, ...rest } = form;
          await updateModel(editing.id, rest);
        } else {
          await updateModel(editing.id, form);
        }
        addToast('Model updated', 'success');
      } else {
        await createModel(form);
        addToast('Model created', 'success');
      }
      setModalOpen(false);
      await loadModels();
    } catch (err) {
      console.error('Save failed:', err);
      addToast(err instanceof Error ? err.message : 'Save failed', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (model: NousModel) => {
    const ok = await confirm({
      title: 'Delete Model',
      message: `Are you sure you want to delete "${model.display_name}"? This action cannot be undone.`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await deleteModel(model.id);
      addToast('Model deleted', 'success');
      await loadModels();
    } catch (err) {
      console.error('Delete failed:', err);
      addToast('Failed to delete model', 'error');
    }
  };

  const handleToggleEnabled = async (model: NousModel) => {
    try {
      await updateModel(model.id, { is_enabled: !model.is_enabled });
      setModels((prev) =>
        prev.map((m) => (m.id === model.id ? { ...m, is_enabled: !m.is_enabled } : m)),
      );
    } catch (err) {
      console.error('Toggle failed:', err);
      addToast('Failed to update model', 'error');
    }
  };

  return (
    <div className="min-h-screen bg-ink-950 text-ink-100">
      <div className="max-w-6xl mx-auto px-6 py-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
        {/* Header */}
        <div className="flex items-center gap-4 mb-2">
          <button
            onClick={() => navigate(-1)}
            className="p-2 rounded-lg text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-indigo-500/10 rounded-xl flex items-center justify-center">
              <Brain size={20} className="text-indigo-400" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-ink-50">Nous Models</h1>
              <p className="text-xs text-ink-500">Manage AI model configurations</p>
            </div>
          </div>
        </div>

        {/* Toolbar */}
        <div className="flex items-center justify-between mb-6 mt-6">
          <p className="text-sm text-ink-500">
            {models.length} model{models.length !== 1 ? 's' : ''} configured
          </p>
          <button onClick={openCreate}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors">
            <Plus size={16} />
            Add Model
          </button>
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={28} className="animate-spin text-ink-500" />
          </div>
        ) : models.length === 0 ? (
          <div className="text-center py-20">
            <Brain size={40} className="mx-auto text-ink-700 mb-3" />
            <p className="text-ink-500 text-sm">No models configured yet</p>
            <button onClick={openCreate}
              className="mt-4 text-sm text-indigo-400 hover:text-indigo-300 transition-colors">
              Add your first model
            </button>
          </div>
        ) : (
          <div className="bg-ink-900 border border-ink-800 rounded-xl overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-ink-800 text-left">
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider">Name</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider">Category</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider">Provider : Model</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider">API Key</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider">Pricing</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider text-center">Enabled</th>
                  <th className="px-4 py-3 text-xs font-medium text-ink-500 uppercase tracking-wider text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-ink-800/50">
                {models.map((model) => (
                  <tr key={model.id} className="hover:bg-ink-800/30 transition-colors">
                    <td className="px-4 py-3">
                      <div className="font-medium text-ink-200">{model.display_name}</div>
                      <div className="text-xs text-ink-500 mt-0.5">{model.name}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex px-2 py-0.5 text-xs font-medium rounded-full border ${CATEGORY_COLORS[model.category] ?? ''}`}>
                        {CATEGORY_LABELS[model.category] ?? model.category}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-ink-300">
                      <span className="text-ink-400">{model.actual_provider}</span>
                      <span className="text-ink-600 mx-1">:</span>
                      <span>{model.actual_model}</span>
                    </td>
                    <td className="px-4 py-3">
                      <code className="text-xs text-ink-500 bg-ink-800 px-1.5 py-0.5 rounded">
                        {model.api_key_masked}
                      </code>
                    </td>
                    <td className="px-4 py-3 text-ink-300">
                      <span className="font-mono">{model.pricing_value}</span>
                      <span className="text-ink-500 text-xs ml-1">{PRICING_LABELS[model.pricing_type] ?? ''}</span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <button onClick={() => handleToggleEnabled(model)} className="inline-flex">
                        {model.is_enabled
                          ? <ToggleRight size={22} className="text-indigo-400" />
                          : <ToggleLeft size={22} className="text-ink-600" />}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button onClick={() => openEdit(model)}
                          className="p-1.5 rounded-lg text-ink-500 hover:text-ink-200 hover:bg-ink-800 transition-colors">
                          <Pencil size={15} />
                        </button>
                        <button onClick={() => handleDelete(model)}
                          className="p-1.5 rounded-lg text-ink-500 hover:text-red-400 hover:bg-red-500/10 transition-colors">
                          <Trash2 size={15} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Modal */}
      {modalOpen && (
        <NousModelFormModal
          editing={editing}
          form={form}
          saving={saving}
          onFormChange={handleFormChange}
          onSave={handleSave}
          onClose={() => setModalOpen(false)}
        />
      )}
    </div>
  );
}

export default AdminNousModelsPage;
