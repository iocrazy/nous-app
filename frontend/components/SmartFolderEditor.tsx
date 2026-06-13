import React, { useState, useCallback } from 'react';
import { X, Plus, Minus, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { SmartFolderRules, SmartFolderCondition } from '../services/resourceService';

// ─── Field / Operator definitions ───────────────────────

interface FieldDef {
  key: string;
  labelKey: string;
  operators: { key: string; labelKey: string }[];
  inputType: 'text' | 'select' | 'number';
  options?: { value: string; labelKey: string }[];
}

const FIELDS: FieldDef[] = [
  {
    key: 'filename',
    labelKey: 'smartFolder.fields.filename',
    operators: [
      { key: 'contains', labelKey: 'smartFolder.ops.contains' },
      { key: 'eq', labelKey: 'smartFolder.ops.equals' },
      { key: 'starts_with', labelKey: 'smartFolder.ops.startsWith' },
    ],
    inputType: 'text',
  },
  {
    key: 'file_type',
    labelKey: 'smartFolder.fields.fileType',
    operators: [
      { key: 'eq', labelKey: 'smartFolder.ops.equals' },
    ],
    inputType: 'select',
    options: [
      { value: 'video', labelKey: 'smartFolder.fileTypes.video' },
      { value: 'audio', labelKey: 'smartFolder.fileTypes.audio' },
      { value: 'image', labelKey: 'smartFolder.fileTypes.image' },
      { value: 'document', labelKey: 'smartFolder.fileTypes.document' },
      { value: 'other', labelKey: 'smartFolder.fileTypes.other' },
    ],
  },
  {
    key: 'source_type',
    labelKey: 'smartFolder.fields.sourceType',
    operators: [
      { key: 'eq', labelKey: 'smartFolder.ops.equals' },
    ],
    inputType: 'select',
    options: [
      { value: 'upload', labelKey: 'smartFolder.sourceTypes.upload' },
      { value: 'web', labelKey: 'smartFolder.sourceTypes.web' },
    ],
  },
  {
    key: 'mime_type',
    labelKey: 'smartFolder.fields.mimeType',
    operators: [
      { key: 'eq', labelKey: 'smartFolder.ops.equals' },
      { key: 'contains', labelKey: 'smartFolder.ops.contains' },
    ],
    inputType: 'text',
  },
  {
    key: 'file_size_bytes',
    labelKey: 'smartFolder.fields.fileSize',
    operators: [
      { key: 'gt', labelKey: 'smartFolder.ops.greaterThan' },
      { key: 'lt', labelKey: 'smartFolder.ops.lessThan' },
      { key: 'gte', labelKey: 'smartFolder.ops.gte' },
      { key: 'lte', labelKey: 'smartFolder.ops.lte' },
    ],
    inputType: 'number',
  },
  {
    key: 'created_at',
    labelKey: 'smartFolder.fields.createdAt',
    operators: [
      { key: 'gt', labelKey: 'smartFolder.ops.greaterThan' },
      { key: 'lt', labelKey: 'smartFolder.ops.lessThan' },
      { key: 'gte', labelKey: 'smartFolder.ops.gte' },
      { key: 'lte', labelKey: 'smartFolder.ops.lte' },
    ],
    inputType: 'text',
  },
  {
    key: 'duration_seconds',
    labelKey: 'smartFolder.fields.duration',
    operators: [
      { key: 'gt', labelKey: 'smartFolder.ops.greaterThan' },
      { key: 'lt', labelKey: 'smartFolder.ops.lessThan' },
    ],
    inputType: 'number',
  },
  {
    key: 'resolution',
    labelKey: 'smartFolder.fields.resolution',
    operators: [
      { key: 'eq', labelKey: 'smartFolder.ops.equals' },
      { key: 'contains', labelKey: 'smartFolder.ops.contains' },
    ],
    inputType: 'text',
  },
  {
    key: 'tags',
    labelKey: 'smartFolder.fields.tags',
    operators: [
      { key: 'contains', labelKey: 'smartFolder.ops.contains' },
      { key: 'not_contains', labelKey: 'smartFolder.ops.notContains' },
    ],
    inputType: 'text',
  },
];

function getFieldDef(key: string): FieldDef {
  return FIELDS.find((f) => f.key === key) || FIELDS[0];
}

function defaultCondition(): SmartFolderCondition {
  const field = FIELDS[0];
  return { field: field.key, op: field.operators[0].key, value: '' };
}

// ─── Props ──────────────────────────────────────────────

interface SmartFolderEditorProps {
  onSave: (name: string, rules: SmartFolderRules) => Promise<void>;
  onClose: () => void;
  initialName?: string;
  initialRules?: SmartFolderRules;
}

// ─── Component ──────────────────────────────────────────

export const SmartFolderEditor: React.FC<SmartFolderEditorProps> = ({
  onSave,
  onClose,
  initialName = '',
  initialRules,
}) => {
  const { t } = useTranslation();

  const [name, setName] = useState(initialName);
  const [operator, setOperator] = useState<'AND' | 'OR'>(initialRules?.operator || 'AND');
  const [match, setMatch] = useState(initialRules?.match ?? true);
  const [conditions, setConditions] = useState<SmartFolderCondition[]>(
    initialRules?.conditions?.length ? initialRules.conditions : [defaultCondition()]
  );
  const [saving, setSaving] = useState(false);

  const updateCondition = useCallback((index: number, patch: Partial<SmartFolderCondition>) => {
    setConditions((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }, []);

  const addCondition = useCallback(() => {
    setConditions((prev) => [...prev, defaultCondition()]);
  }, []);

  const removeCondition = useCallback((index: number) => {
    setConditions((prev) => (prev.length <= 1 ? prev : prev.filter((_, i) => i !== index)));
  }, []);

  const handleFieldChange = useCallback((index: number, fieldKey: string) => {
    const def = getFieldDef(fieldKey);
    setConditions((prev) =>
      prev.map((c, i) =>
        i === index
          ? { field: fieldKey, op: def.operators[0].key, value: def.options?.[0]?.value || '' }
          : c
      )
    );
  }, []);

  const handleSave = async () => {
    if (!name.trim() || conditions.length === 0) return;
    setSaving(true);
    try {
      await onSave(name.trim(), { operator, match, conditions });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-ink-900 border border-ink-700 rounded-xl w-full max-w-2xl shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-ink-800">
          <div className="flex items-center gap-2">
            <Zap size={18} className="text-amber-400" />
            <h2 className="text-base font-semibold text-ink-100">
              {initialRules ? t('smartFolder.editTitle') : t('smartFolder.createTitle')}
            </h2>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-300 transition-colors">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-4 max-h-[70vh] overflow-y-auto">
          {/* Name */}
          <div>
            <label className="text-xs text-ink-400 mb-1 block">{t('smartFolder.name')}</label>
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('smartFolder.namePlaceholder')}
              className="w-full bg-ink-800 border border-ink-700 rounded-lg px-3 py-2 text-sm text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500 transition-colors"
            />
          </div>

          {/* Rule header: Match [all|any] ... are [true|false] */}
          <div className="flex items-center gap-2 text-sm text-ink-300">
            <span>{t('smartFolder.matchPrefix')}</span>
            <select
              value={operator}
              onChange={(e) => setOperator(e.target.value as 'AND' | 'OR')}
              className="bg-ink-800 border border-ink-700 rounded px-2 py-1 text-sm text-ink-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="AND">{t('smartFolder.matchAll')}</option>
              <option value="OR">{t('smartFolder.matchAny')}</option>
            </select>
            <span>{t('smartFolder.matchMiddle')}</span>
            <select
              value={match ? 'true' : 'false'}
              onChange={(e) => setMatch(e.target.value === 'true')}
              className="bg-ink-800 border border-ink-700 rounded px-2 py-1 text-sm text-ink-200 focus:outline-none focus:border-indigo-500"
            >
              <option value="true">{t('smartFolder.matchTrue')}</option>
              <option value="false">{t('smartFolder.matchFalse')}</option>
            </select>
          </div>

          {/* Condition rows */}
          <div className="space-y-2">
            {conditions.map((cond, idx) => {
              const fieldDef = getFieldDef(cond.field);
              return (
                <div key={idx} className="flex items-center gap-2">
                  {/* Field */}
                  <select
                    value={cond.field}
                    onChange={(e) => handleFieldChange(idx, e.target.value)}
                    className="bg-ink-800 border border-ink-700 rounded px-2 py-1.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 min-w-[120px]"
                  >
                    {FIELDS.map((f) => (
                      <option key={f.key} value={f.key}>
                        {t(f.labelKey)}
                      </option>
                    ))}
                  </select>

                  {/* Operator */}
                  <select
                    value={cond.op}
                    onChange={(e) => updateCondition(idx, { op: e.target.value })}
                    className="bg-ink-800 border border-ink-700 rounded px-2 py-1.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500 min-w-[100px]"
                  >
                    {fieldDef.operators.map((op) => (
                      <option key={op.key} value={op.key}>
                        {t(op.labelKey)}
                      </option>
                    ))}
                  </select>

                  {/* Value */}
                  {fieldDef.inputType === 'select' && fieldDef.options ? (
                    <select
                      value={cond.value}
                      onChange={(e) => updateCondition(idx, { value: e.target.value })}
                      className="flex-1 bg-ink-800 border border-ink-700 rounded px-2 py-1.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500"
                    >
                      {fieldDef.options.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {t(opt.labelKey)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={fieldDef.inputType === 'number' ? 'number' : 'text'}
                      value={cond.value}
                      onChange={(e) => updateCondition(idx, { value: e.target.value })}
                      placeholder={
                        cond.field === 'created_at'
                          ? 'relative:-7d or 2026-01-01'
                          : cond.field === 'file_size_bytes'
                          ? '1048576 (bytes)'
                          : ''
                      }
                      className="flex-1 bg-ink-800 border border-ink-700 rounded px-2 py-1.5 text-sm text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500"
                    />
                  )}

                  {/* Add / Remove buttons */}
                  <button
                    onClick={addCondition}
                    className="p-1 text-ink-500 hover:text-ink-300 transition-colors"
                    title={t('smartFolder.addCondition')}
                  >
                    <Plus size={16} />
                  </button>
                  <button
                    onClick={() => removeCondition(idx)}
                    disabled={conditions.length <= 1}
                    className="p-1 text-ink-500 hover:text-ink-300 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    title={t('smartFolder.removeCondition')}
                  >
                    <Minus size={16} />
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-5 py-3 border-t border-ink-800">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-ink-400 hover:text-ink-200 transition-colors"
          >
            {t('common.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={!name.trim() || conditions.length === 0 || saving}
            className="px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {saving ? t('common.saving') : t('common.save')}
          </button>
        </div>
      </div>
    </div>
  );
};
