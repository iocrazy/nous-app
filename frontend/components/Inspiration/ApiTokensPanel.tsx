// Personal Access Token manager for the Inspiration library (P4c-2). Lets a
// user mint `mhk_...` tokens and drive the notes API from external scripts or
// shortcuts. Self-contained state: loads its own list, owns the create flow,
// the one-time plaintext reveal, and per-row revoke confirmation.
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Copy, Key, Plus, Check } from 'lucide-react';
import { useToast } from '../Toast';
import { getApiUrl } from '../../utils/apiConfig';
import {
  createToken,
  listTokens,
  revokeToken,
  type ApiToken,
  type ApiTokenCreated,
} from '../../services/inspirationService';

function fmtDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

export const ApiTokensPanel: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<ApiTokenCreated | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const rows = await listTokens();
        if (alive) setTokens(rows);
      } catch (err) {
        console.error('listTokens failed', err);
        if (alive) addToast(t('inspiration.tokens.loadFailed', 'Failed to load tokens'), 'error');
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
    // addToast/t are context-stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submitCreate = async () => {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    try {
      const token = await createToken(trimmed);
      // Prepend the row without the plaintext secret; surface the secret
      // separately in the one-time reveal so it never lands in the list state.
      const { token: _secret, ...row } = token;
      setTokens((prev) => [row, ...prev]);
      setCreated(token);
      setCreating(false);
      setName('');
    } catch (err) {
      console.error('createToken failed', err);
      addToast(t('inspiration.tokens.createFailed', 'Failed to create token'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const doRevoke = async (id: string) => {
    if (busy) return;
    setBusy(true);
    const snapshot = tokens;
    // Optimistic removal; restore the snapshot on failure so the list never
    // drifts from the server.
    setTokens((prev) => prev.filter((x) => x.id !== id));
    setConfirmId(null);
    try {
      await revokeToken(id);
      addToast(t('inspiration.tokens.revoked', 'Token revoked'), 'success');
    } catch (err) {
      console.error('revokeToken failed', err);
      setTokens(snapshot);
      addToast(t('inspiration.tokens.revokeFailed', 'Failed to revoke token'), 'error');
    } finally {
      setBusy(false);
    }
  };

  const copySecret = async () => {
    if (!created) return;
    try {
      await navigator.clipboard?.writeText(created.token);
      setCopied(true);
      addToast(t('inspiration.tokens.copied', 'Copied'), 'success');
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.error('clipboard write failed', err);
      addToast(t('inspiration.tokens.copyFailed', 'Copy failed'), 'error');
    }
  };

  const curl = [
    `curl -X POST ${getApiUrl()}/api/v1/inspiration/notes \\`,
    '  -H "Authorization: Bearer mhk_..." \\',
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"content_md":"idea #tag"}'`,
  ].join('\n');

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-content-3">
        {t(
          'inspiration.tokens.intro',
          'Use a token to write notes into your library from external scripts or shortcuts.',
        )}
      </p>

      {/* One-time plaintext reveal */}
      {created && (
        <div className="rounded-lg border border-[var(--accent-border)] bg-[var(--accent-soft)] p-3">
          <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--accent-text)]">
            {t('inspiration.tokens.newTokenTitle', 'New token created')}
          </div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 select-all break-all rounded bg-island-2 px-2 py-1.5 font-mono text-xs text-content">
              {created.token}
            </code>
            <button
              onClick={() => void copySecret()}
              className="inline-flex shrink-0 items-center gap-1 rounded-lg bg-indigo-500 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400"
            >
              {copied ? <Check size={13} /> : <Copy size={13} />}
              {copied ? t('inspiration.tokens.copiedShort', 'Copied') : t('inspiration.tokens.copy', 'Copy')}
            </button>
          </div>
          <p className="mt-2 text-[11px] text-amber-400">
            {t('inspiration.tokens.onceWarning', 'This token is shown only once. Store it now.')}
          </p>
          <button
            onClick={() => setCreated(null)}
            className="mt-2 rounded-lg bg-island-2 px-3 py-1 text-xs text-content-2 hover:bg-line"
          >
            {t('inspiration.tokens.done', 'Done')}
          </button>
        </div>
      )}

      {/* Create control */}
      {creating ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) void submitCreate();
              if (e.key === 'Escape') {
                setCreating(false);
                setName('');
              }
            }}
            placeholder={t('inspiration.tokens.namePlaceholder', 'Token name (e.g. iphone-shortcut)')}
            className="min-w-0 flex-1 rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content placeholder:text-content-4 focus:outline-none"
          />
          <button
            onClick={() => void submitCreate()}
            disabled={busy || !name.trim()}
            className="shrink-0 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-400 disabled:opacity-40"
          >
            {t('inspiration.tokens.create', 'Create')}
          </button>
          <button
            onClick={() => {
              setCreating(false);
              setName('');
            }}
            className="shrink-0 rounded-lg bg-island-2 px-3 py-1.5 text-xs text-content-2 hover:bg-line"
          >
            {t('inspiration.tokens.cancel', 'Cancel')}
          </button>
        </div>
      ) : (
        <button
          onClick={() => setCreating(true)}
          className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-text)] hover:bg-[var(--accent-soft)]"
        >
          <Plus size={13} />
          {t('inspiration.tokens.newToken', 'New Token')}
        </button>
      )}

      {/* Token list */}
      {loading ? (
        <div className="py-6 text-center text-xs text-content-4">
          {t('inspiration.tokens.loading', 'Loading…')}
        </div>
      ) : tokens.length === 0 ? (
        <div className="flex flex-col items-center gap-1.5 rounded-lg bg-island-2 py-8 text-center">
          <Key size={18} className="text-content-4" />
          <div className="text-sm font-medium text-content-2">
            {t('inspiration.tokens.empty', 'No API tokens yet')}
          </div>
          <div className="max-w-xs text-xs text-content-4">
            {t(
              'inspiration.tokens.emptyHint',
              'Create a token to write notes into your library from external scripts or shortcuts.',
            )}
          </div>
        </div>
      ) : (
        <div className="divide-y divide-line overflow-hidden rounded-lg bg-island-2">
          {tokens.map((tk) => (
            <div key={tk.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-content">{tk.name}</div>
                <div className="mt-0.5 flex items-center gap-2 text-[11px] text-content-4">
                  <span>{t('inspiration.tokens.created', 'Created')} {fmtDate(tk.created_at)}</span>
                  <span aria-hidden="true">·</span>
                  <span>
                    {tk.last_used_at
                      ? `${t('inspiration.tokens.lastUsed', 'Last used')} ${fmtDate(tk.last_used_at)}`
                      : t('inspiration.tokens.neverUsed', 'Never used')}
                  </span>
                </div>
              </div>
              {confirmId === tk.id ? (
                <div className="flex shrink-0 items-center gap-1.5">
                  <button
                    onClick={() => void doRevoke(tk.id)}
                    disabled={busy}
                    className="rounded-lg bg-red-500/90 px-2.5 py-1 text-xs font-semibold text-white hover:bg-red-500 disabled:opacity-40"
                  >
                    {t('inspiration.tokens.confirmRevoke', 'Confirm')}
                  </button>
                  <button
                    onClick={() => setConfirmId(null)}
                    className="rounded-lg bg-island px-2.5 py-1 text-xs text-content-2 hover:bg-line"
                  >
                    {t('inspiration.tokens.cancel', 'Cancel')}
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmId(tk.id)}
                  className="shrink-0 rounded-lg bg-island px-2.5 py-1 text-xs font-medium text-red-400 hover:bg-line"
                >
                  {t('inspiration.tokens.revoke', 'Revoke')}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Usage example */}
      <div>
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-content-3">
          {t('inspiration.tokens.exampleTitle', 'Example')}
        </div>
        <pre className="overflow-x-auto rounded-lg bg-island-2 p-3 font-mono text-[11px] leading-relaxed text-content-2">
          {curl}
        </pre>
      </div>
    </div>
  );
};
