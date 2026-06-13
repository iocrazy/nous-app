/**
 * A — MCP Servers settings panel.
 *
 * List the user's registered outbound MCP servers + add/edit/delete.
 * Each server has: name (namespace), URL, optional bearer token,
 * description, enabled flag.
 *
 * Token handling:
 *  - Backend never returns the bearer_token; only has_bearer_token bool
 *  - Add/edit form lets user type a new token; leaving blank on edit
 *    means "keep existing token"
 *  - List shows 🔒 indicator when has_bearer_token=true, no token text
 */
import React, { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Plug,
  Plus,
  RefreshCw,
  Edit3,
  Trash2,
  Lock,
  Power,
  PowerOff,
  X,
  Check,
} from 'lucide-react';
import { aiLibraryService } from '../services/aiLibraryService';
import { useToast } from './Toast';
import type { AILibraryMCPServer } from '../types';
import {
  EMPTY_MCP_FORM as EMPTY_FORM,
  buildMCPServerUpdatePayload,
  validateMCPServerForm,
  type MCPServerFormValues,
} from './MCPServersPanel.helpers';

export const MCPServersPanel: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [items, setItems] = useState<AILibraryMCPServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<MCPServerFormValues>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await aiLibraryService.listMCPServers();
      setItems(resp.items);
    } catch (err) {
      console.error('[MCPServersPanel] load failed:', err);
      addToast(t('mcp.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast, t]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const startCreate = useCallback(() => {
    setForm(EMPTY_FORM);
    setEditingId(null);
    setCreating(true);
  }, []);

  const startEdit = useCallback((server: AILibraryMCPServer) => {
    setForm({
      name: server.name,
      url: server.url,
      bearer_token: '', // empty = keep existing
      description: server.description || '',
      enabled: server.enabled,
    });
    setEditingId(server.id);
    setCreating(false);
  }, []);

  const cancelForm = useCallback(() => {
    setEditingId(null);
    setCreating(false);
    setForm(EMPTY_FORM);
  }, []);

  const validate = useCallback(
    (): string | null => validateMCPServerForm(form, creating),
    [form, creating],
  );

  const handleSubmit = useCallback(async () => {
    const err = validate();
    if (err) {
      addToast(t(err), 'error');
      return;
    }
    setSubmitting(true);
    try {
      if (creating) {
        await aiLibraryService.createMCPServer({
          name: form.name.trim(),
          url: form.url.trim(),
          bearer_token: form.bearer_token.trim() || undefined,
          description: form.description.trim() || undefined,
          enabled: form.enabled,
        });
        addToast(t('mcp.addedToast'), 'success');
      } else if (editingId) {
        await aiLibraryService.updateMCPServer(
          editingId,
          buildMCPServerUpdatePayload(form),
        );
        addToast(t('mcp.updatedToast'), 'success');
      }
      cancelForm();
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(t('mcp.saveFailed', { message: msg }), 'error');
    } finally {
      setSubmitting(false);
    }
  }, [creating, editingId, form, validate, reload, cancelForm, addToast, t]);

  const handleDelete = useCallback(
    async (server: AILibraryMCPServer) => {
      if (
        !window.confirm(
          t('mcp.deleteConfirm', { name: server.name }),
        )
      ) {
        return;
      }
      try {
        await aiLibraryService.deleteMCPServer(server.id);
        addToast(t('mcp.deletedToast'), 'info');
        await reload();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        addToast(t('mcp.deleteFailed', { message: msg }), 'error');
      }
    },
    [addToast, reload, t],
  );

  const handleToggleEnabled = useCallback(
    async (server: AILibraryMCPServer) => {
      // L3: optimistic update — flip locally first so the UI doesn't
      // freeze while the round-trip lands. Revert on failure.
      const target = !server.enabled;
      setItems((prev) =>
        prev.map((s) => (s.id === server.id ? { ...s, enabled: target } : s)),
      );
      try {
        await aiLibraryService.updateMCPServer(server.id, { enabled: target });
      } catch (e) {
        // Revert on failure
        setItems((prev) =>
          prev.map((s) =>
            s.id === server.id ? { ...s, enabled: server.enabled } : s,
          ),
        );
        const msg = e instanceof Error ? e.message : String(e);
        addToast(t('mcp.toggleFailed', { message: msg }), 'error');
      }
    },
    [addToast, t],
  );

  const showForm = creating || editingId !== null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-ink-800">
        <div className="flex items-center gap-2">
          <Plug className="w-4 h-4 text-blue-400" />
          <h2 className="text-sm font-semibold text-ink-200">{t('mcp.title')}</h2>
          {items.length > 0 && (
            <span className="text-xs text-ink-500">({items.length})</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reload}
            className="p-1.5 rounded hover:bg-ink-800 text-ink-500 hover:text-ink-300 transition-colors"
            title={t('mcp.refresh')}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
          {!showForm && (
            <button
              type="button"
              onClick={startCreate}
              className="flex items-center gap-1 px-2 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white transition-colors"
            >
              <Plus className="w-3 h-3" />
              {t('mcp.addServer')}
            </button>
          )}
        </div>
      </div>

      {/* Help text */}
      <div className="px-4 py-2 bg-ink-900/50 border-b border-ink-800 text-[11px] text-ink-500">
        {t('mcp.helpText')}
      </div>

      {/* Add/Edit form */}
      {showForm && (
        <div className="border-b border-ink-800 bg-ink-900/30 p-4 space-y-3">
          <div className="text-xs font-medium text-ink-400">
            {creating ? t('mcp.addModalTitle') : t('mcp.editModalTitle')}
          </div>

          {creating && (
            <div>
              <label className="block text-[11px] text-ink-500 mb-1">
                {t('mcp.namespaceLabel')} *
              </label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="notion"
                className="w-full px-2 py-1 text-sm rounded bg-ink-900 border border-ink-800 text-ink-200 focus:outline-none focus:border-blue-600"
              />
              <div className="text-[10px] text-ink-600 mt-0.5">
                {t('mcp.namespaceHint')}
              </div>
            </div>
          )}

          <div>
            <label className="block text-[11px] text-ink-500 mb-1">{t('mcp.urlLabel')} *</label>
            <input
              type="url"
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="https://mcp.example.com/jsonrpc"
              className="w-full px-2 py-1 text-sm rounded bg-ink-900 border border-ink-800 text-ink-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <div>
            <label className="block text-[11px] text-ink-500 mb-1">
              {creating ? t('mcp.tokenLabel') : t('mcp.tokenLabelEdit')}
            </label>
            <input
              type="password"
              value={form.bearer_token}
              onChange={(e) =>
                setForm((f) => ({ ...f, bearer_token: e.target.value }))
              }
              placeholder={t('mcp.tokenPlaceholder')}
              className="w-full px-2 py-1 text-sm rounded bg-ink-900 border border-ink-800 text-ink-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <div>
            <label className="block text-[11px] text-ink-500 mb-1">
              {t('mcp.descriptionLabel')}
            </label>
            <input
              type="text"
              value={form.description}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
              placeholder={t('mcp.descriptionPlaceholder')}
              className="w-full px-2 py-1 text-sm rounded bg-ink-900 border border-ink-800 text-ink-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-ink-400 cursor-pointer">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) =>
                setForm((f) => ({ ...f, enabled: e.target.checked }))
              }
            />
            {t('mcp.enabledLabel')}
          </label>

          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              disabled={submitting}
              onClick={handleSubmit}
              className="flex items-center gap-1 px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50"
            >
              <Check className="w-3 h-3" />
              {creating ? t('mcp.add') : t('mcp.save')}
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={cancelForm}
              className="flex items-center gap-1 px-3 py-1 text-xs rounded bg-ink-800 hover:bg-ink-700 text-ink-300 transition-colors disabled:opacity-50"
            >
              <X className="w-3 h-3" />
              {t('mcp.cancel')}
            </button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && items.length === 0 ? (
          <div className="text-center py-8 text-xs text-ink-500">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 text-sm text-ink-500">
            <Plug className="w-8 h-8 mx-auto mb-2 opacity-30" />
            <p>{t('mcp.emptyTitle')}</p>
            <p className="text-xs text-ink-600 mt-1">
              {t('mcp.emptyHint')}
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-ink-800">
            {items.map((server) => (
              <li
                key={server.id}
                className="px-4 py-3 hover:bg-ink-900/40 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          server.enabled ? 'text-ink-200' : 'text-ink-500'
                        }`}
                      >
                        {server.name}
                      </span>
                      {server.has_bearer_token && (
                        <Lock
                          className="w-3 h-3 text-amber-500"
                          title={t('mcp.tokenConfigured')}
                        />
                      )}
                      {!server.enabled && (
                        <span className="text-[10px] uppercase text-ink-600 tracking-wider">
                          {t('mcp.disabledLabel')}
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-ink-500 truncate mt-0.5">
                      {server.url}
                    </div>
                    {server.description && (
                      <div className="text-xs text-ink-600 mt-0.5 italic">
                        {server.description}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      type="button"
                      onClick={() => handleToggleEnabled(server)}
                      className="p-1.5 rounded hover:bg-ink-800 transition-colors"
                      title={server.enabled ? t('mcp.disable') : t('mcp.enable')}
                    >
                      {server.enabled ? (
                        <Power className="w-3.5 h-3.5 text-green-500" />
                      ) : (
                        <PowerOff className="w-3.5 h-3.5 text-ink-500" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => startEdit(server)}
                      className="p-1.5 rounded hover:bg-ink-800 text-ink-500 hover:text-ink-300 transition-colors"
                      title={t('mcp.edit')}
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(server)}
                      className="p-1.5 rounded hover:bg-red-900/30 text-ink-500 hover:text-red-400 transition-colors"
                      title={t('mcp.delete')}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
};

export default MCPServersPanel;
