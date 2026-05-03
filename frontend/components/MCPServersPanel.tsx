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

const NAME_RE = /^[A-Za-z0-9_]+$/;

interface MCPServerFormValues {
  name: string;
  url: string;
  bearer_token: string;
  description: string;
  enabled: boolean;
}

const EMPTY_FORM: MCPServerFormValues = {
  name: '',
  url: '',
  bearer_token: '',
  description: '',
  enabled: true,
};

export const MCPServersPanel: React.FC = () => {
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
      addToast('Load MCP servers failed', 'error');
    } finally {
      setLoading(false);
    }
  }, [addToast]);

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

  const validate = useCallback((): string | null => {
    if (creating) {
      if (!form.name.trim()) return 'Name is required';
      if (!NAME_RE.test(form.name))
        return 'Name must be alphanumeric + underscore (no dots)';
    }
    if (!form.url.trim()) return 'URL is required';
    if (!/^https?:\/\//.test(form.url))
      return 'URL must start with http:// or https://';
    return null;
  }, [form, creating]);

  const handleSubmit = useCallback(async () => {
    const err = validate();
    if (err) {
      addToast(err, 'error');
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
        addToast('MCP server added', 'success');
      } else if (editingId) {
        const patch: Parameters<typeof aiLibraryService.updateMCPServer>[1] = {
          url: form.url.trim(),
          description: form.description.trim() || undefined,
          enabled: form.enabled,
        };
        if (form.bearer_token.trim()) {
          patch.bearer_token = form.bearer_token.trim();
        }
        await aiLibraryService.updateMCPServer(editingId, patch);
        addToast('MCP server updated', 'success');
      }
      cancelForm();
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast(`Save failed: ${msg}`, 'error');
    } finally {
      setSubmitting(false);
    }
  }, [creating, editingId, form, validate, reload, cancelForm, addToast]);

  const handleDelete = useCallback(
    async (server: AILibraryMCPServer) => {
      if (
        !window.confirm(
          `Delete MCP server "${server.name}"? Tools from this server will no longer be available to your agents.`,
        )
      ) {
        return;
      }
      try {
        await aiLibraryService.deleteMCPServer(server.id);
        addToast('MCP server deleted', 'info');
        await reload();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        addToast(`Delete failed: ${msg}`, 'error');
      }
    },
    [addToast, reload],
  );

  const handleToggleEnabled = useCallback(
    async (server: AILibraryMCPServer) => {
      try {
        await aiLibraryService.updateMCPServer(server.id, {
          enabled: !server.enabled,
        });
        await reload();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        addToast(`Toggle failed: ${msg}`, 'error');
      }
    },
    [addToast, reload],
  );

  const showForm = creating || editingId !== null;

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <Plug className="w-4 h-4 text-blue-400" />
          <h2 className="text-sm font-semibold text-zinc-200">MCP Servers</h2>
          {items.length > 0 && (
            <span className="text-xs text-zinc-500">({items.length})</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={reload}
            className="p-1.5 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
            title="Refresh"
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
              Add Server
            </button>
          )}
        </div>
      </div>

      {/* Help text */}
      <div className="px-4 py-2 bg-zinc-900/50 border-b border-zinc-800 text-[11px] text-zinc-500">
        Outbound MCP servers expose tools your agents can call. Server name
        becomes a namespace prefix on tool names (e.g.{' '}
        <code className="text-zinc-400">notion.create_page</code>). Use only
        servers you trust — agents may invoke tools that have side effects.
      </div>

      {/* Add/Edit form */}
      {showForm && (
        <div className="border-b border-zinc-800 bg-zinc-900/30 p-4 space-y-3">
          <div className="text-xs font-medium text-zinc-400">
            {creating ? 'Add MCP Server' : 'Edit MCP Server'}
          </div>

          {creating && (
            <div>
              <label className="block text-[11px] text-zinc-500 mb-1">
                Name (namespace) *
              </label>
              <input
                type="text"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="notion"
                className="w-full px-2 py-1 text-sm rounded bg-zinc-900 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-600"
              />
              <div className="text-[10px] text-zinc-600 mt-0.5">
                Alphanumeric + underscore only. Cannot be changed later.
              </div>
            </div>
          )}

          <div>
            <label className="block text-[11px] text-zinc-500 mb-1">URL *</label>
            <input
              type="url"
              value={form.url}
              onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
              placeholder="https://mcp.example.com/jsonrpc"
              className="w-full px-2 py-1 text-sm rounded bg-zinc-900 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <div>
            <label className="block text-[11px] text-zinc-500 mb-1">
              Bearer Token {!creating && '(blank = keep existing)'}
            </label>
            <input
              type="password"
              value={form.bearer_token}
              onChange={(e) =>
                setForm((f) => ({ ...f, bearer_token: e.target.value }))
              }
              placeholder="optional"
              className="w-full px-2 py-1 text-sm rounded bg-zinc-900 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <div>
            <label className="block text-[11px] text-zinc-500 mb-1">
              Description
            </label>
            <input
              type="text"
              value={form.description}
              onChange={(e) =>
                setForm((f) => ({ ...f, description: e.target.value }))
              }
              placeholder="Personal Notion workspace"
              className="w-full px-2 py-1 text-sm rounded bg-zinc-900 border border-zinc-800 text-zinc-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) =>
                setForm((f) => ({ ...f, enabled: e.target.checked }))
              }
            />
            Enabled (server's tools available to agents)
          </label>

          <div className="flex items-center gap-2 pt-1">
            <button
              type="button"
              disabled={submitting}
              onClick={handleSubmit}
              className="flex items-center gap-1 px-3 py-1 text-xs rounded bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50"
            >
              <Check className="w-3 h-3" />
              {creating ? 'Add' : 'Save'}
            </button>
            <button
              type="button"
              disabled={submitting}
              onClick={cancelForm}
              className="flex items-center gap-1 px-3 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 transition-colors disabled:opacity-50"
            >
              <X className="w-3 h-3" />
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {loading && items.length === 0 ? (
          <div className="text-center py-8 text-xs text-zinc-500">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 text-sm text-zinc-500">
            <Plug className="w-8 h-8 mx-auto mb-2 opacity-30" />
            <p>No MCP servers registered yet.</p>
            <p className="text-xs text-zinc-600 mt-1">
              Click "Add Server" to register an outbound MCP endpoint.
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-zinc-800">
            {items.map((server) => (
              <li
                key={server.id}
                className="px-4 py-3 hover:bg-zinc-900/40 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          server.enabled ? 'text-zinc-200' : 'text-zinc-500'
                        }`}
                      >
                        {server.name}
                      </span>
                      {server.has_bearer_token && (
                        <Lock
                          className="w-3 h-3 text-amber-500"
                          title="Bearer token configured"
                        />
                      )}
                      {!server.enabled && (
                        <span className="text-[10px] uppercase text-zinc-600 tracking-wider">
                          disabled
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-zinc-500 truncate mt-0.5">
                      {server.url}
                    </div>
                    {server.description && (
                      <div className="text-xs text-zinc-600 mt-0.5 italic">
                        {server.description}
                      </div>
                    )}
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      type="button"
                      onClick={() => handleToggleEnabled(server)}
                      className="p-1.5 rounded hover:bg-zinc-800 transition-colors"
                      title={server.enabled ? 'Disable' : 'Enable'}
                    >
                      {server.enabled ? (
                        <Power className="w-3.5 h-3.5 text-green-500" />
                      ) : (
                        <PowerOff className="w-3.5 h-3.5 text-zinc-500" />
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => startEdit(server)}
                      className="p-1.5 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
                      title="Edit"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(server)}
                      className="p-1.5 rounded hover:bg-red-900/30 text-zinc-500 hover:text-red-400 transition-colors"
                      title="Delete"
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
