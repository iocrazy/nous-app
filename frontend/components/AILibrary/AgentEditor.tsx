// frontend/components/AILibrary/AgentEditor.tsx
// Agent detail shell — three tabs (B2, spec 2026-08-02 §B2).
//
//   Workbench  what it's doing      → AgentWorkbenchTab
//   Persona    who it is            → AgentPersonaTab
//   Profile    its paperwork        → AgentProfileTab
//
// Eight sub-tabs used to sit here; LEGACY_TAB_MAP keeps old ``?tab=`` links
// working. This file keeps what the tabs share: loading the agent, the draft
// and its single Save, the paused/override banners, and tab routing.
//
// Draft state is local; `save()` PATCHes via aiLibraryService and replaces
// the hydrated agent immutably on success.
//
// WARNING: Every hook stays ABOVE the loading/error early-returns. A useState below
// them changes the hook count between the loading and loaded renders and
// blows up with React #310 on every agent open — that regression shipped
// once already (see resetOverride's note).

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type {
  AgentCapabilities,
  AgentChatPermissions,
  AILibraryAgent,
  AILibrarySkill,
  NousModelPublic,
} from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { getNousModels, getAIGovernance } from '../../services/aiService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { AlertTriangle, Play } from 'lucide-react';
import { NewAgentModal } from './NewAgentModal';
import { AgentActionBar } from './AgentActionBar';
import { AgentWorkbenchTab } from './AgentWorkbenchTab';
import { AgentPersonaTab } from './AgentPersonaTab';
import { AgentProfileTab } from './AgentProfileTab';
import PermissionsSection from './PermissionsSection';
import { PROVIDER_DISPLAY_NAMES, getAvailableModels } from './agentEditorModel';
import { GROUP_AVATAR, agentGroupOf } from './agentStatus';
import { getAgentIcon } from './agentIcons';
import { useGlobalChatStore } from '../../stores/globalChatStore';

type SubTab = 'workbench' | 'persona' | 'permissions' | 'profile';

/**
 * Where each of the old eight sub-tabs went (B2, spec 2026-08-02 §B2).
 * Bookmarks and in-app links carrying the old ``?tab=`` values keep working
 * instead of silently landing on the default tab.
 */
export const LEGACY_TAB_MAP: Record<string, SubTab> = {
  dashboard: 'workbench',
  runs: 'workbench',
  routines: 'workbench',
  overview: 'persona',
  files: 'persona',
  skills: 'persona',
  versions: 'profile',
};

export const SUB_TABS: SubTab[] = ['workbench', 'persona', 'permissions', 'profile'];

/** English fallbacks: `t()` with no default renders the raw key path when a
 *  locale is missing one, which in a tab strip looks like a broken label. */
const SUB_TAB_LABELS: Record<SubTab, string> = {
  workbench: 'Workbench',
  persona: 'Persona & Skills',
  permissions: 'Permissions',
  profile: 'Profile',
};

/** Resolve a ``?tab=`` value (new or legacy) to a tab; unknown → workbench. */
export function resolveSubTab(raw: string | null | undefined): SubTab {
  if (raw && SUB_TABS.includes(raw as SubTab)) return raw as SubTab;
  return (raw && LEGACY_TAB_MAP[raw]) || 'workbench';
}

/**
 * Lift a useful message out of an error. Backend errors come back as
 * Error.message with the shape `"403: {\"success\":false,\"error\":\"...
 * \",\"code\":\"http_403\",...}"` (from aiLibraryService). Showing that
 * raw JSON in a toast is hostile; lift the ``error`` / ``detail`` field
 * if we can parse it, otherwise fall back to the plain message.
 */
function friendlyError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  // Common shape: "<status>: <json>" — strip status prefix, parse json.
  const match = raw.match(/^\d{3}:\s*(\{.*\})\s*$/s);
  if (match) {
    try {
      const parsed = JSON.parse(match[1]);
      const lifted = parsed.error || parsed.detail || parsed.message;
      if (typeof lifted === 'string' && lifted.length > 0) {
        return lifted;
      }
    } catch {
      // fall through to raw
    }
  }
  return raw;
}

interface AgentEditorProps {
  slug: string;
  /**
   * Called after a successful fork. Parent should refresh its agent list and
   * (ideally) select the new slug so the user lands on their fresh copy.
   */
  onAgentForked?: (newSlug: string) => void;
  /** Called after this agent is deleted so the parent can navigate away. */
  onAgentDeleted?: () => void;
}

export const AgentEditor: React.FC<AgentEditorProps> = ({ slug, onAgentForked, onAgentDeleted }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // Same derivation as AILibraryLayout — the workbench links into the team's
  // issue list, which needs the team segment.
  const { teamId } = useParams();
  const urlPrefix = teamId ? `/team/${teamId}` : '';
  const { userProfile, aiSettings } = useAuth();
  const { addToast } = useToast();
  const requestChat = useGlobalChatStore((s) => s.requestChat);
  // Admin escape removed (2026-05-19 QA): backend rejects PATCH on system
  // presets for everyone including admins, so admin-only UI affordances
  // were a foot-gun — admin would click 保存更改 and hit a 403 with raw
  // JSON error replacing the editor. Phase 1 spec is read-only for ALL
  // users; UI now matches.
  // ``userProfile.role === 'admin'`` is still useful elsewhere (Workforce,
  // approval queue) — keeping the deconstruct above so callers don't break.
  void userProfile;
  const [nousLlm, setNousLlm] = useState<NousModelPublic[]>([]);
  const [nousEnabled, setNousEnabled] = useState(false);

  useEffect(() => {
    getNousModels('llm').then(setNousLlm).catch(() => {});
    getAIGovernance()
      .then((g) => setNousEnabled(Boolean(g.nous_enabled)))
      .catch(() => setNousEnabled(false));
  }, []);

  const modelGroups = useMemo(() => {
    const base = getAvailableModels(aiSettings);
    if (nousEnabled && nousLlm.length > 0) {
      base.push({
        providerKey: 'nous',
        providerName: PROVIDER_DISPLAY_NAMES.nous,
        models: nousLlm.map((m) => m.name),
      });
    }
    return base;
  }, [aiSettings, nousEnabled, nousLlm]);
  const [agent, setAgent] = useState<AILibraryAgent | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const sub = resolveSubTab(searchParams.get('tab'));
  const setSub = useCallback(
    (next: SubTab) => {
      // replace: tab switches shouldn't stack history entries between the
      // gallery and the agent the user came from.
      const params = new URLSearchParams(searchParams);
      params.set('tab', next);
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );
  const [draft, setDraft] = useState<Partial<AILibraryAgent>>({});
  const [permDraft, setPermDraft] = useState<AgentChatPermissions>(() => ({}));
  const [capsDraft, setCapsDraft] = useState<AgentCapabilities>(() => ({}));
  const [localSkillIds, setLocalSkillIds] = useState<number[]>([]);
  const [allSkills, setAllSkills] = useState<AILibrarySkill[] | null>(null);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [permSaving, setPermSaving] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [allAgents, setAllAgents] = useState<AILibraryAgent[]>([]);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setAgent(null);
    setError(null);
    setLocalSkillIds([]);

    aiLibraryService
      .getAgent(slug)
      .then((a) => {
        if (cancelled) return;
        setAgent(a);
        setDraft(buildDraft(a));
        setLocalSkillIds(a.skill_ids);
        setPermDraft(a.chat_permissions ?? {});
        setCapsDraft(a.capabilities ?? {});
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentEditor] getAgent failed:', err);
        setError(friendlyError(err));
      });

    return () => {
      cancelled = true;
    };
  }, [slug]);

  // Lazily load the full skill catalog the first time the Skills tab is opened.
  // Cached on the editor instance — reused across tab switches until the agent
  // slug changes (which remounts the effect via the loadSkills dependency).
  useEffect(() => {
    // skillsLoading must NOT be a dep: setSkillsLoading(true) below would
    // retrigger this effect, run cleanup on the previous pass, flip
    // `cancelled=true`, and the in-flight fetch would then silently no-op in
    // both its .then and .finally — UI stuck on "Loading skills..." forever.
    if (sub !== 'persona' || allSkills !== null) return;
    let cancelled = false;
    setSkillsLoading(true);
    aiLibraryService
      .listSkills()
      .then((list) => {
        if (cancelled) return;
        setAllSkills(list);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentEditor] listSkills failed:', err);
        addToast(
          t('aiLibrary.agents.loadSkillsError', { error: friendlyError(err) }),
          'error',
        );
      })
      .finally(() => {
        if (!cancelled) setSkillsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sub, allSkills, addToast, t]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-300">
        {t('aiLibrary.agents.loadErrorPrefix')}: {error}
      </div>
    );
  }

  if (!agent) {
    return <div className="text-sm text-ink-500">{t('common.loading')}</div>;
  }

  const isPreset = agent.is_system_preset;
  // Agent-overrides (mig 341): preset CONTENT (prompts / model / temperature /
  // max_tokens) is editable by everyone — the backend routes those edits into
  // the caller's personal override layer (reset anytime). Catalog identity
  // (name/description/icon), enabled flag, skills and budgets stay admin-owned.
  const catalogLocked = isPreset;
  const readOnly = false;

  // Provider preflight: the agent's model belongs to a provider the
  // current user hasn't configured (no API key / disabled). Invocations
  // would fail at the LLM call step; surface this as a banner instead
  // of leaving it as a 5-char "(提供方未启用)" suffix in the model dropdown
  // that users miss.
  const enabledModels = new Set(modelGroups.flatMap((g) => g.models));
  const modelProviderDisabled = !!agent.model && !enabledModels.has(agent.model);
  const firstEnabledModel = modelGroups[0]?.models?.[0];

  const switchToFirstEnabled = async () => {
    if (readOnly || !firstEnabledModel) return;
    setSaving(true);
    try {
      const updated = await aiLibraryService.updateAgent(slug, {
        model: firstEnabledModel,
      });
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(
        t('aiLibrary.agents.modelSwitched', 'Switched to {{model}}', {
          model: firstEnabledModel,
        }),
        'success',
      );
    } catch (err) {
      console.error('[AgentEditor] switchToFirstEnabled failed:', err);
      addToast(
        `${t('aiLibrary.agents.modelSwitchFailed', 'Failed to switch model')}: ${err instanceof Error ? err.message : String(err)}`,
        'error',
      );
    } finally {
      setSaving(false);
    }
  };

  // Skills dirty check — structural compare of the ordered id array.
  const skillsDirty =
    JSON.stringify(localSkillIds) !== JSON.stringify(agent.skill_ids);

  const save = async (): Promise<void> => {
    if (readOnly) return;
    setSaving(true);
    // Don't touch ``error`` here — a save failure is transient and must
    // not replace the editor surface (the user needs to see the form to
    // recover). Reserve ``setError`` for the load path only.
    try {
      // Build the PATCH payload immutably from the overview/files draft, and
      // attach ``skill_ids`` only when the Skills tab has pending changes so
      // we don't wipe+rewrite bindings on unrelated saves.
      let patch: Partial<AILibraryAgent> =
        skillsDirty && !catalogLocked
          ? { ...draft, skill_ids: localSkillIds }
          : { ...draft };
      if (catalogLocked) {
        // System preset: send ONLY the override-able content fields —
        // catalog fields are admin-owned and would 403.
        const { identity_md, soul_md, agent_md, model, temperature, max_tokens } =
          patch;
        patch = { identity_md, soul_md, agent_md, model, temperature, max_tokens };
      }
      const updated = await aiLibraryService.updateAgent(slug, patch);
      setAgent(updated);
      setDraft(buildDraft(updated));
      setLocalSkillIds(updated.skill_ids);
      const message = skillsDirty
        ? t('aiLibrary.agents.skillsUpdatedToast', 'Skills updated')
        : t('aiLibrary.agents.saved', 'Agent saved');
      addToast(message, 'success');
    } catch (err) {
      console.error('[AgentEditor] updateAgent failed:', err);
      addToast(
        t('aiLibrary.agents.saveError', { error: friendlyError(err) }),
        'error',
      );
    } finally {
      setSaving(false);
    }
  };

  /**
   * Resume from a paused state (budget or manual). Clears `paused_reason`
   * via a dedicated endpoint so the PATCH route's exclude-null semantics
   * stay uniform. If the agent is still over its monthly budget, the
   * sweeper will re-pause within ~60 s — callers should bump the budget
   * first to avoid the flap.
   */
  // Hard-delete this user-owned agent (presets never see the menu item).
  const handleDeleteAgent = async (): Promise<void> => {
    // eslint-disable-next-line no-alert
    if (!window.confirm(
      t('aiLibrary.agents.deleteConfirm',
        'Delete this agent? Its runs and skill bindings are removed; chat history stays readable.'),
    )) return;
    try {
      await aiLibraryService.deleteAgent(slug);
      addToast(t('aiLibrary.agents.deletedToast', 'Agent deleted'), 'success');
      // Sidebar keeps its own agents list — nudge it to reload.
      window.dispatchEvent(new CustomEvent('ai-library:agents-changed'));
      onAgentDeleted?.();
    } catch (err) {
      console.error('[AgentEditor] deleteAgent failed:', err);
      addToast(
        t('aiLibrary.agents.saveError', { error: friendlyError(err) }),
        'error',
      );
    }
  };

  // 复位: drop the caller's override layer so the preset falls back to the
  // admin/system defaults (DELETE /agents/{slug}/override). NOTE: its
  // `resetting` state is declared with the other hooks at the top — a
  // useState down here sat AFTER the loading/error early-returns and blew
  // up every agent open with React #310 (hooks count changed between the
  // loading render and the loaded render).
  const resetOverride = async (): Promise<void> => {
    setResetting(true);
    try {
      const updated = await aiLibraryService.deleteAgentOverride(slug);
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(
        t('aiLibrary.agents.overrideResetToast', 'Restored system defaults'),
        'success',
      );
    } catch (err) {
      console.error('[AgentEditor] deleteAgentOverride failed:', err);
      addToast(
        t('aiLibrary.agents.saveError', { error: friendlyError(err) }),
        'error',
      );
    } finally {
      setResetting(false);
    }
  };

  const handleResume = async (): Promise<void> => {
    if (catalogLocked) return;
    setResuming(true);
    setError(null);
    try {
      const updated = await aiLibraryService.resumeAgent(slug);
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(
        t('aiLibrary.agents.budget.resumedToast', 'Agent resumed'),
        'success',
      );
    } catch (err) {
      console.error('[AgentEditor] resumeAgent failed:', err);
      addToast(
        t('aiLibrary.agents.resumeError', { error: friendlyError(err) }),
        'error',
      );
    } finally {
      setResuming(false);
    }
  };

  /**
   * Permissions save their own way: their own role gate, deliberately NOT
   * folded into the header Save (permissions are governance, editable even on
   * presets whose content is locked).
   *
   * Chat perms and high-risk capabilities go in ONE PATCH — they are sibling
   * subtrees the backend merges independently, and one request keeps the tab's
   * Save atomic. Both drafts are re-seeded from the response so a value the
   * server clamped or refused shows up immediately instead of the UI keeping a
   * grant that never landed.
   */
  const savePermissions = async (): Promise<void> => {
    setPermSaving(true);
    try {
      const updated = await aiLibraryService.updateAgentPermissions(agent.slug, {
        chat_permissions: permDraft,
        capabilities: capsDraft,
      });
      setAgent(updated);
      setPermDraft(updated.chat_permissions ?? {});
      setCapsDraft(updated.capabilities ?? {});
      addToast(t('aiLibrary.agents.saved', 'Agent saved'), 'success');
    } catch (err) {
      console.error('[AgentEditor] updateAgentPermissions failed:', err);
      addToast(t('aiLibrary.agents.saveError', { error: friendlyError(err) }), 'error');
    } finally {
      setPermSaving(false);
    }
  };

  // ─── Skill binding helpers (local-state only; PATCH on Save) ───────────────

  const addSkill = (skillId: number): void => {
    if (catalogLocked) return;
    setLocalSkillIds((ids) =>
      ids.includes(skillId) ? ids : [...ids, skillId],
    );
  };

  const removeSkill = (skillId: number): void => {
    if (catalogLocked) return;
    setLocalSkillIds((ids) => ids.filter((id) => id !== skillId));
  };

  const moveSkill = (skillId: number, direction: -1 | 1): void => {
    if (catalogLocked) return;
    setLocalSkillIds((ids) => {
      const idx = ids.indexOf(skillId);
      if (idx === -1) return ids;
      const next = idx + direction;
      if (next < 0 || next >= ids.length) return ids;
      const copy = [...ids];
      const [moved] = copy.splice(idx, 1);
      copy.splice(next, 0, moved);
      return copy;
    });
  };

  const updateDraft = <K extends keyof AILibraryAgent>(key: K, value: AILibraryAgent[K]) => {
    setDraft((d) => ({ ...d, [key]: value }));
  };

  /**
   * Open the fork modal. We lazily fetch the full agent list so the Fork-from
   * dropdown inside <NewAgentModal> has something to show if the user wants to
   * pick a different source after opening.
   */
  const openForkModal = async () => {
    setForkModalOpen(true);
    if (allAgents.length === 0) {
      try {
        const list = await aiLibraryService.listAgents();
        setAllAgents(list);
      } catch (err) {
        console.error('[AgentEditor] listAgents for fork failed:', err);
      }
    }
  };

  const handleForkCreated = (newSlug: string) => {
    setForkModalOpen(false);
    addToast(
      t('aiLibrary.agents.forkedToast', 'Forked as {{slug}}', { slug: newSlug }),
      'success',
    );
    onAgentForked?.(newSlug);
  };

  return (
    <div>
      <header className="mb-4 flex items-center gap-3">
        <span
          className={`grid h-11 w-11 shrink-0 place-items-center rounded-lg ${GROUP_AVATAR[agentGroupOf(agent)]}`}
        >
          {React.createElement(getAgentIcon(agent.icon), { size: 21 })}
        </span>
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-ink-100">
            <span className="truncate">{agent.name}</span>
            <ScopeBadge agent={agent} />
          </h2>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-ink-500">
            <span className="font-mono">{agent.slug}</span>
            <span>·</span>
            <span>{agent.model}</span>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <AgentActionBar
            agent={agent}
            readOnly={catalogLocked}
            onAgentUpdated={(updated) => {
              setAgent(updated);
              setDraft(buildDraft(updated));
            }}
            onDuplicate={openForkModal}
            onDelete={isPreset ? undefined : handleDeleteAgent}
          />
          {/* Save only exists on the tab that has a draft. Workbench and
              Profile edit nothing through this button. */}
          {sub === 'persona' && (
            <button
              onClick={save}
              disabled={saving}
              className="rounded-lg border border-ink-700 bg-ink-800 px-4 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
            >
              {saving ? t('common.saving') : t('aiLibrary.agents.saveChanges')}
            </button>
          )}
          <button
            type="button"
            onClick={() => requestChat(agent.slug)}
            className="rounded-lg bg-ok px-5 py-2 text-sm font-semibold text-white transition-colors hover:opacity-90 whitespace-nowrap"
          >
            {t('aiLibrary.agents.startChat', 'Start chat')}
          </button>
        </div>
      </header>

      {modelProviderDisabled && (
        <div className="mb-4 rounded-lg banner-amber px-4 py-3 text-sm flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="font-medium banner-amber-strong">
              {t(
                'aiLibrary.agents.providerDisabledTitle',
                'Provider not configured',
              )}
            </div>
            <div className="text-xs opacity-90">
              {t(
                'aiLibrary.agents.providerDisabledBody',
                "Model {{model}} belongs to a provider this account hasn't configured. Invocations of this agent will fail. Pick another model below or configure the provider in AI Settings.",
                { model: agent.model },
              )}
            </div>
          </div>
          <div className="shrink-0 flex items-center gap-2">
            {!readOnly && firstEnabledModel && (
              <button
                onClick={switchToFirstEnabled}
                disabled={saving}
                className="rounded-md btn-tint-amber px-3 py-1.5 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                {t('aiLibrary.agents.switchToModel', 'Switch to {{model}}', {
                  model: firstEnabledModel,
                })}
              </button>
            )}
            <button
              onClick={() => navigate('/settings?tab=ai')}
              className="rounded-md btn-tint-amber px-3 py-1.5 text-xs font-medium transition-colors whitespace-nowrap"
            >
              {t('aiLibrary.agents.goToAISettings', 'Go to AI Settings')}
            </button>
          </div>
        </div>
      )}

      <nav className="mb-4 flex gap-1 border-b border-ink-800">
        {SUB_TABS.map((k) => (
          <button
            key={k}
            onClick={() => setSub(k)}
            className={`px-3 py-2 text-sm font-medium transition-colors -mb-px border-b-2 ${
              sub === k
                ? 'border-[var(--accent-border)] text-ink-100'
                : 'border-transparent text-ink-500 hover:text-ink-300'
            }`}
          >
            {t(`aiLibrary.agents.tab.${k}`, SUB_TAB_LABELS[k])}
          </button>
        ))}
      </nav>

      {sub === 'workbench' && (
        <AgentWorkbenchTab agent={agent} slug={slug} urlPrefix={urlPrefix} />
      )}

      {sub === 'persona' && (
        <AgentPersonaTab
          agent={agent}
          draft={draft}
          updateDraft={updateDraft}
          readOnly={readOnly}
          catalogLocked={catalogLocked}
          modelGroups={modelGroups}
          localSkillIds={localSkillIds}
          allSkills={allSkills}
          skillsLoading={skillsLoading}
          onAddSkill={addSkill}
          onRemoveSkill={removeSkill}
          onMoveSkill={moveSkill}
        />
      )}

      {sub === 'permissions' && (
        <section className="max-w-2xl">
          <PermissionsSection
            value={permDraft}
            onChange={setPermDraft}
            capabilities={capsDraft}
            onCapabilitiesChange={setCapsDraft}
          />
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              disabled={permSaving}
              onClick={savePermissions}
              className="rounded-lg border border-ink-700 bg-ink-800 px-4 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50"
            >
              {permSaving ? t('common.saving') : t('common.save', 'Save')}
            </button>
          </div>
        </section>
      )}

      {sub === 'profile' && (
        <AgentProfileTab
          agent={agent}
          slug={slug}
          draft={draft}
          updateDraft={updateDraft}
          catalogLocked={catalogLocked}
          onRollback={() => {
            void aiLibraryService.getAgent(slug).then((a) => {
              setAgent(a);
              setDraft(buildDraft(a));
            });
          }}
        />
      )}

      {forkModalOpen && (
        <NewAgentModal
          existingAgents={allAgents.length > 0 ? allAgents : [agent]}
          initialForkFrom={agent.slug}
          onClose={() => setForkModalOpen(false)}
          onCreated={handleForkCreated}
        />
      )}
    </div>
  );
};

/**
 * Tiny presentational badge showing the agent's scope: system preset,
 * team-scoped, project-scoped, or private (falls back to showing nothing
 * for now when ``is_system_preset=false`` and both team/project are null —
 * keeps the header uncluttered for the common per-user case).
 */
const ScopeBadge: React.FC<{ agent: AILibraryAgent }> = ({ agent }) => {
  const { t } = useTranslation();
  const base =
    'ml-1 rounded border px-2 py-0.5 whitespace-nowrap';

  if (agent.is_system_preset) {
    return (
      <span className={`${base} border-ink-700 bg-ink-800 text-ink-300`}>
        {t('aiLibrary.officialTemplate', 'Official template')}
      </span>
    );
  }
  if (agent.team_id != null) {
    return (
      <span className={`${base} border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent-text)]`}>
        {t('aiLibrary.agents.scopeBadgeTeam', 'Team: {{name}}', {
          name: agent.team_name ?? agent.team_id,
        })}
      </span>
    );
  }
  if (agent.project_id != null) {
    return (
      <span className={`${base} border-emerald-500/40 bg-emerald-500/10 text-emerald-300`}>
        {t('aiLibrary.agents.scopeBadgeProject', 'Project: {{name}}', {
          name: agent.project_name ?? agent.project_id,
        })}
      </span>
    );
  }
  return (
    <span className={`${base} border-ink-700 bg-ink-900 text-ink-400`}>
      {t('aiLibrary.agents.scopeBadgePrivate', 'Private')}
    </span>
  );
};

/**
 * Build the editable draft subset from a hydrated agent. Keeps keys that the
 * Overview + Files forms edit; omits server-managed fields (id, timestamps,
 * is_system_preset, skill_ids, ownership FKs).
 */
function buildDraft(a: AILibraryAgent): Partial<AILibraryAgent> {
  // Round temperature to one decimal so a noisy DB value (e.g.
  // 0.6999999988079071 from 0.1+0.1+0.1+... seed) gets cleaned up on
  // first save instead of being faithfully re-PATCHed.
  const tidyTemp = Number.isFinite(a.temperature)
    ? Math.round((a.temperature as number) * 10) / 10
    : a.temperature;
  return {
    name: a.name,
    description: a.description ?? '',
    icon: a.icon ?? null,
    model: a.model,
    temperature: tidyTemp,
    max_tokens: a.max_tokens,
    enabled: a.enabled,
    identity_md: a.identity_md ?? '',
    soul_md: a.soul_md ?? '',
    agent_md: a.agent_md ?? '',
    monthly_token_budget: a.monthly_token_budget ?? null,
    monthly_cost_cents_budget: a.monthly_cost_cents_budget ?? null,
    timeout_sec: a.timeout_sec ?? null,
    max_concurrent_runs: a.max_concurrent_runs ?? null,
  };
}


/**
 * Top-of-Overview banner shown when the agent has a `paused_reason` set.
 *
 * - `'budget'` (amber): the sweeper found spend over the cap. Resume clears
 *   the flag, but if the budget isn't raised first, the sweeper will
 *   re-pause within ~60 s — the copy says so.
 * - `'manual'` (zinc): an admin / owner hit Pause. Resume re-enables.
 *
 * The Resume button is also disabled for preset agents (readOnly) since
 * Phase 1 policy blocks writes on system presets.
 */
const PausedBanner: React.FC<{
  reason: 'budget' | 'manual';
  disabled: boolean;
  resuming: boolean;
  onResume: () => void;
}> = ({ reason, disabled, resuming, onResume }) => {
  const { t } = useTranslation();
  const isBudget = reason === 'budget';
  const wrap = isBudget
    ? 'border-amber-500/40 bg-amber-500/10 text-warn-soft'
    : 'border-ink-700 bg-ink-800 text-ink-200';
  const icon = isBudget ? 'text-amber-400' : 'text-ink-400';
  const title = isBudget
    ? t('aiLibrary.agents.budget.pausedTitleBudget', 'Paused — monthly budget exceeded')
    : t('aiLibrary.agents.budget.pausedTitleManual', 'Paused manually');
  const body = isBudget
    ? t(
        'aiLibrary.agents.budget.pausedBodyBudget',
        'The sweeper detected this agent ran over its token or cost budget this month. Raise the budget below before resuming — otherwise the sweeper will re-pause within a minute.',
      )
    : t(
        'aiLibrary.agents.budget.pausedBodyManual',
        'An owner or admin paused this agent. Click Resume to re-enable.',
      );
  return (
    <div className={`flex items-start gap-3 rounded-lg border px-3 py-3 text-xs ${wrap}`}>
      <AlertTriangle size={16} className={`flex-shrink-0 mt-0.5 ${icon}`} />
      <div className="flex-1 min-w-0">
        <div className="font-medium">{title}</div>
        <p className="mt-0.5 opacity-90">{body}</p>
      </div>
      <button
        type="button"
        onClick={onResume}
        disabled={disabled}
        className="inline-flex items-center gap-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 px-2.5 py-1.5 text-xs font-medium text-emerald-300 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40 whitespace-nowrap"
      >
        <Play size={12} />
        {resuming
          ? t('aiLibrary.agents.budget.resuming', 'Resuming...')
          : t('aiLibrary.agents.budget.resume', 'Resume')}
      </button>
    </div>
  );
};

/**
 * Budget input row. Two numeric fields: monthly token budget and monthly
 * cost budget (in cents). Empty / 0 means unlimited (the backend normalizes
 * 0 → NULL before persisting, so the sweeper's `is not None` cap check
 * treats both the same way).
 *
 * Uses string-valued inputs internally so the user can cleanly delete all
 * digits without React emitting spurious 0s or NaN — we parse on change and
 * send `null` back up when the field is empty.
 */
export default AgentEditor;
