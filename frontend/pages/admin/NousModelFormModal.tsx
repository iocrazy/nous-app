import React from 'react';
import { X, Loader2, ToggleLeft, ToggleRight } from 'lucide-react';

// ---------------------------------------------------------------------------
// Shared types & constants
// ---------------------------------------------------------------------------

export interface NousModel {
  readonly id: string;
  readonly name: string;
  readonly display_name: string;
  readonly category: 'transcription' | 'summarization' | 'analysis';
  readonly actual_provider: string;
  readonly actual_model: string;
  readonly api_key_masked: string;
  readonly app_id?: string;
  readonly base_url?: string;
  readonly pricing_type: 'per_hour' | 'per_request' | 'per_token';
  readonly pricing_value: number;
  readonly is_enabled: boolean;
  readonly sort_order: number;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface ModelFormData {
  readonly name: string;
  readonly display_name: string;
  readonly category: 'transcription' | 'summarization' | 'analysis';
  readonly actual_provider: string;
  readonly actual_model: string;
  readonly api_key: string;
  readonly app_id: string;
  readonly base_url: string;
  readonly pricing_type: 'per_hour' | 'per_request' | 'per_token';
  readonly pricing_value: number;
  readonly is_enabled: boolean;
  readonly sort_order: number;
}

export const EMPTY_FORM: ModelFormData = {
  name: '',
  display_name: '',
  category: 'transcription',
  actual_provider: '',
  actual_model: '',
  api_key: '',
  app_id: '',
  base_url: '',
  pricing_type: 'per_token',
  pricing_value: 0,
  is_enabled: true,
  sort_order: 0,
};

export const CATEGORY_LABELS: Record<string, string> = {
  transcription: 'Transcription',
  summarization: 'Summarization',
  analysis: 'Analysis',
};

export const PRICING_LABELS: Record<string, string> = {
  per_hour: '/ hour',
  per_request: '/ request',
  per_token: '/ token',
};

export const CATEGORY_COLORS: Record<string, string> = {
  transcription: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  summarization: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
  analysis: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
};

// ---------------------------------------------------------------------------
// Form field component
// ---------------------------------------------------------------------------

const Field: React.FC<{
  label: string;
  children: React.ReactNode;
  hint?: string;
}> = ({ label, children, hint }) => (
  <div>
    <label className="block text-xs font-medium text-ink-400 mb-1.5">{label}</label>
    {children}
    {hint && <p className="text-[11px] text-ink-600 mt-1">{hint}</p>}
  </div>
);

const inputClass =
  'w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 ' +
  'placeholder-ink-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500';

const selectClass =
  'w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 ' +
  'focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:border-indigo-500';

// ---------------------------------------------------------------------------
// Modal
// ---------------------------------------------------------------------------

interface NousModelFormModalProps {
  readonly editing: NousModel | null;
  readonly form: ModelFormData;
  readonly saving: boolean;
  readonly onFormChange: (patch: Partial<ModelFormData>) => void;
  readonly onSave: () => void;
  readonly onClose: () => void;
}

export const NousModelFormModal: React.FC<NousModelFormModalProps> = ({
  editing, form, saving, onFormChange, onSave, onClose,
}) => (
  <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
    <div className="bg-ink-900 border border-ink-700 rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-ink-800">
        <h2 className="text-lg font-semibold text-ink-100">
          {editing ? 'Edit Model' : 'Add Model'}
        </h2>
        <button onClick={onClose} className="p-1 rounded-lg text-ink-500 hover:text-ink-300 hover:bg-ink-800 transition-colors">
          <X size={18} />
        </button>
      </div>

      {/* Body */}
      <div className="px-6 py-5 space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Name" hint="Internal identifier, e.g. nous-llm">
            <input className={inputClass} value={form.name} placeholder="nous-llm"
              onChange={(e) => onFormChange({ name: e.target.value })} />
          </Field>
          <Field label="Display Name">
            <input className={inputClass} value={form.display_name} placeholder="Nous LLM (Volcengine 2.0)"
              onChange={(e) => onFormChange({ display_name: e.target.value })} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Category">
            <select className={selectClass} value={form.category}
              onChange={(e) => onFormChange({ category: e.target.value as ModelFormData['category'] })}>
              <option value="transcription">Transcription</option>
              <option value="summarization">Summarization</option>
              <option value="analysis">Analysis</option>
            </select>
          </Field>
          <Field label="Sort Order">
            <input type="number" className={inputClass} value={form.sort_order}
              onChange={(e) => onFormChange({ sort_order: Number(e.target.value) })} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Provider" hint="e.g. volcengine, openai">
            <input className={inputClass} value={form.actual_provider} placeholder="volcengine"
              onChange={(e) => onFormChange({ actual_provider: e.target.value })} />
          </Field>
          <Field label="Model" hint="e.g. seed-asr, gpt-4o">
            <input className={inputClass} value={form.actual_model} placeholder="seed-asr"
              onChange={(e) => onFormChange({ actual_model: e.target.value })} />
          </Field>
        </div>

        <Field label="API Key" hint={editing ? 'Leave blank to keep current key' : undefined}>
          <input type="password" className={inputClass} value={form.api_key}
            placeholder={editing ? '(unchanged)' : 'sk-...'}
            onChange={(e) => onFormChange({ api_key: e.target.value })} />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="App ID" hint="Optional, provider-specific">
            <input className={inputClass} value={form.app_id} placeholder="Optional"
              onChange={(e) => onFormChange({ app_id: e.target.value })} />
          </Field>
          <Field label="Base URL" hint="Optional, custom endpoint">
            <input className={inputClass} value={form.base_url} placeholder="https://..."
              onChange={(e) => onFormChange({ base_url: e.target.value })} />
          </Field>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Pricing Type">
            <select className={selectClass} value={form.pricing_type}
              onChange={(e) => onFormChange({ pricing_type: e.target.value as ModelFormData['pricing_type'] })}>
              <option value="per_hour">Per Hour</option>
              <option value="per_request">Per Request</option>
              <option value="per_token">Per Token</option>
            </select>
          </Field>
          <Field label="Pricing Value (points)">
            <input type="number" className={inputClass} value={form.pricing_value} min={0}
              onChange={(e) => onFormChange({ pricing_value: Number(e.target.value) })} />
          </Field>
        </div>

        <div className="flex items-center gap-3 pt-1">
          <button
            type="button"
            onClick={() => onFormChange({ is_enabled: !form.is_enabled })}
            className="flex items-center gap-2 text-sm text-ink-300"
          >
            {form.is_enabled
              ? <ToggleRight size={24} className="text-indigo-400" />
              : <ToggleLeft size={24} className="text-ink-500" />}
            <span>{form.is_enabled ? 'Enabled' : 'Disabled'}</span>
          </button>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-ink-800">
        <button onClick={onClose}
          className="px-4 py-2 text-sm font-medium text-ink-400 hover:text-ink-200 rounded-lg hover:bg-ink-800 transition-colors">
          Cancel
        </button>
        <button onClick={onSave} disabled={saving}
          className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50 flex items-center gap-2">
          {saving && <Loader2 size={14} className="animate-spin" />}
          {editing ? 'Save Changes' : 'Create Model'}
        </button>
      </div>
    </div>
  </div>
);
