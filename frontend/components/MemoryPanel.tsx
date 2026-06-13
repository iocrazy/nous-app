import React, { useCallback, useEffect, useState } from 'react';
import { Brain, Trash2, Loader2, Save, AlertTriangle, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  getMemoryProfile,
  setMemoryPrefs,
  setMemoryCard,
  deleteMemoryObservation,
  forgetAllMemory,
  type MemoryProfile,
} from '../services/memoryService';

/**
 * Claude-style memory management. Lets the user see what the AI has
 * learned about them (Honcho observations), toggle learning/injection,
 * edit their self-curated "About me" card, delete single observations,
 * and forget everything. Self-contained section — mirrors the other
 * AISettings sub-panels (MCPServersPanel etc.).
 */
export const MemoryPanel: React.FC = () => {
  const { t } = useTranslation();
  const [profile, setProfile] = useState<MemoryProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cardDraft, setCardDraft] = useState('');
  const [savingCard, setSavingCard] = useState(false);
  const [confirmForget, setConfirmForget] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const p = await getMemoryProfile();
      setProfile(p);
      setCardDraft((p.card || []).join('\n'));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load memory');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (key: 'learn_enabled' | 'inject_enabled') => {
    if (!profile) return;
    const next = !profile[key];
    setProfile({ ...profile, [key]: next });
    try {
      await setMemoryPrefs({ [key]: next });
    } catch (err) {
      setProfile({ ...profile, [key]: !next }); // revert on failure
      setError(err instanceof Error ? err.message : 'Failed to update');
    }
  };

  const saveCard = async () => {
    setSavingCard(true);
    setError(null);
    try {
      const lines = cardDraft
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean);
      await setMemoryCard(lines);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSavingCard(false);
    }
  };

  const removeObservation = async (id: string) => {
    if (!profile) return;
    setProfile({
      ...profile,
      observations: profile.observations.filter((o) => o.id !== id),
    });
    try {
      await deleteMemoryObservation(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete');
      load();
    }
  };

  const forgetAll = async () => {
    setBusy(true);
    setError(null);
    try {
      await forgetAllMemory();
      setConfirmForget(false);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to forget');
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-8 bg-zinc-900/40 border border-zinc-800 rounded-lg overflow-hidden">
      <div className="px-6 py-4 flex items-center gap-3 border-b border-zinc-800">
        <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400">
          <Brain size={18} />
        </div>
        <div className="flex-1">
          <h3 className="font-semibold text-zinc-200">{t('memory.title')}</h3>
          <p className="text-xs text-zinc-500 mt-0.5">{t('memory.subtitle')}</p>
        </div>
      </div>

      <div className="p-6 space-y-6">
        {error && (
          <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            <AlertTriangle size={14} />
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-zinc-500">
            <Loader2 size={16} className="animate-spin" />
            {t('common.loading')}
          </div>
        ) : !profile ? null : (
          <>
            {/* Toggles */}
            <div className="space-y-3">
              <ToggleRow
                label={t('memory.learnLabel')}
                hint={t('memory.learnHint')}
                enabled={profile.learn_enabled}
                onToggle={() => toggle('learn_enabled')}
              />
              <ToggleRow
                label={t('memory.injectLabel')}
                hint={t('memory.injectHint')}
                enabled={profile.inject_enabled}
                onToggle={() => toggle('inject_enabled')}
              />
            </div>

            {!profile.service_available && (
              <p className="text-xs text-amber-400/80">{t('memory.serviceOff')}</p>
            )}

            {/* About me card */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-zinc-400">
                {t('memory.aboutLabel')}
              </label>
              <p className="text-xs text-zinc-500">{t('memory.aboutHint')}</p>
              <textarea
                value={cardDraft}
                onChange={(e) => setCardDraft(e.target.value)}
                rows={4}
                placeholder={t('memory.aboutPlaceholder')}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-purple-500 transition-colors"
              />
              <button
                onClick={saveCard}
                disabled={savingCard}
                className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium bg-zinc-800 text-zinc-200 border border-zinc-700 hover:bg-zinc-700 disabled:opacity-50 transition-colors"
              >
                {savingCard ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}
                {t('memory.saveAbout')}
              </button>
            </div>

            {/* Observations */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-zinc-400">
                {t('memory.observationsLabel')} ({profile.observations.length})
              </label>
              {profile.observations.length === 0 ? (
                <p className="text-xs text-zinc-500">{t('memory.noObservations')}</p>
              ) : (
                <ul className="space-y-1.5">
                  {profile.observations.map((o) => (
                    <li
                      key={o.id}
                      className="group flex items-start gap-2 bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2"
                    >
                      <span className="flex-1 text-xs text-zinc-300 leading-relaxed">
                        {o.content}
                      </span>
                      <button
                        onClick={() => removeObservation(o.id)}
                        title={t('memory.deleteObservation')}
                        className="text-zinc-600 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100"
                      >
                        <Trash2 size={13} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* Forget everything */}
            <div className="pt-2 border-t border-zinc-800/50">
              {!confirmForget ? (
                <button
                  onClick={() => setConfirmForget(true)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-red-400 border border-red-500/30 hover:bg-red-500/10 transition-colors"
                >
                  <Trash2 size={13} />
                  {t('memory.forgetAll')}
                </button>
              ) : (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-zinc-400">{t('memory.forgetConfirm')}</span>
                  <button
                    onClick={forgetAll}
                    disabled={busy}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-500/20 text-red-300 border border-red-500/40 hover:bg-red-500/30 disabled:opacity-50 transition-colors"
                  >
                    {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                    {t('memory.forgetYes')}
                  </button>
                  <button
                    onClick={() => setConfirmForget(false)}
                    className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
                  >
                    <X size={13} />
                    {t('common.cancel')}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
};

const ToggleRow: React.FC<{
  label: string;
  hint: string;
  enabled: boolean;
  onToggle: () => void;
}> = ({ label, hint, enabled, onToggle }) => (
  <div className="flex items-center justify-between gap-4">
    <div className="min-w-0">
      <div className="text-sm text-zinc-200">{label}</div>
      <div className="text-xs text-zinc-500 mt-0.5">{hint}</div>
    </div>
    <button
      onClick={onToggle}
      role="switch"
      aria-checked={enabled}
      className={`relative shrink-0 w-11 h-6 rounded-full transition-colors ${
        enabled ? 'bg-purple-600' : 'bg-zinc-700'
      }`}
    >
      <span
        className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
          enabled ? 'translate-x-5' : ''
        }`}
      />
    </button>
  </div>
);

export default MemoryPanel;
