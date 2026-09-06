// frontend/components/AILibrary/AgentEditor.tsx
// Agent detail shell — five tabs (B2, spec 2026-08-02 §B2).
//
//   Workbench    what it's doing     → AgentWorkbenchTab
//   Persona      who it is           → AgentPersonaTab
//   Permissions  who may talk to it  → PermissionsSection
//   Cost         what it has spent   → AgentCostTab
//   Profile      its paperwork       → AgentProfileTab
//
// Eight sub-tabs used to sit here; LEGACY_TAB_MAP keeps old ``?tab=`` links
// working. This file keeps what the tabs share: loading the agent, the draft
// and its Save, the provider-preflight banner, and tab routing.
//
// Draft state is local; `save()` PATCHes via aiLibraryService and replaces
// the hydrated agent immutably on success.
//
// WARNING: Every hook stays ABOVE the loading/error early-returns. A useState below
// them changes the hook count between the loading and loaded renders and
// blows up with React #310 on every agent open — that regression shipped
// once already, via a `useState` declared next to a handler down in the body.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import type {
  AgentCapabilities,
  AgentChatPermissions,
  AgentPermissionAudit,
  AILibraryAgent,
  AILibrarySkill,
  NousModelPublic,
} from '../../types';
import { aiLibraryService } from '../../services/aiLibraryService';
import { getNousModels, getAIGovernance } from '../../services/aiService';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../Toast';
import { NewAgentModal } from './NewAgentModal';
import { AgentActionBar } from './AgentActionBar';
import { AgentWorkbenchTab } from './AgentWorkbenchTab';
import { AgentPersonaTab } from './AgentPersonaTab';
import { AgentCostTab } from './AgentCostTab';
import { AgentProfileTab } from './AgentProfileTab';
import PermissionsSection from './PermissionsSection';
import PermissionChangeLog from './PermissionChangeLog';
import { PROVIDER_DISPLAY_NAMES, getAvailableModels } from './agentEditorModel';
import { buildModelHealth, healthReasonKey } from '../../utils/modelHealth';
import { formatRelativeTime } from '../../utils/relativeTime';
import { GROUP_AVATAR, agentGroupOf } from './agentStatus';
import { getAgentIcon } from './agentIcons';
import { useGlobalChatStore } from '../../stores/globalChatStore';

type SubTab = 'workbench' | 'persona' | 'permissions' | 'cost' | 'profile';

/**
 * Where each of the old eight sub-tabs went (B2, spec 2026-08-02 §B2).
 * Bookmarks and in-app links carrying the old ``?tab=`` values keep working
 * instead of silently landing on the default tab.
 *
 * ``dashboard`` follows its CONTENT, not its old position: it was the 14-day
 * charts + spend breakdown, which rode into Profile during the rebuild and now
 * lives on Cost. Pointing it at the workbench would land the user on a tab
 * that shares none of what they bookmarked.
 */
export const LEGACY_TAB_MAP: Record<string, SubTab> = {
  dashboard: 'cost',
  runs: 'workbench',
  routines: 'workbench',
  overview: 'persona',
  files: 'persona',
  skills: 'persona',
  versions: 'profile',
};

/**
 * Reading order: what it is doing → who it is → who may talk to it → what it
 * costs → its paperwork.
 *
 * Cost is its own step rather than a block inside Profile. Profile was
 * answering two unrelated questions in one scroll — "how has spend been
 * trending" (charts, usage, 14-day rollup) and "what did this prompt look like
 * last week" (version history) — and the budget inputs at the top made the
 * whole thing read as one page about money that then wasn't.
 */
export const SUB_TABS: SubTab[] = [
  'workbench',
  'persona',
  'permissions',
  'cost',
  'profile',
];

/** English fallbacks: `t()` with no default renders the raw key path when a
 *  locale is missing one, which in a tab strip looks like a broken label. */
const SUB_TAB_LABELS: Record<SubTab, string> = {
  workbench: 'Workbench',
  persona: 'Persona & Skills',
  permissions: 'Permissions',
  cost: 'Cost',
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
        // Show the catalog's display name, not the row id.
        labels: Object.fromEntries(
          nousLlm.map((m) => [m.name, m.display_name || m.name]),
        ),
      });
    }
    return base;
  }, [aiSettings, nousEnabled, nousLlm]);

  // Which platform rows actually run on the USER's machine (backend `is_local`).
  // Derived from the payload rather than matched by name here: "Codex (Local)"
  // is a display string an admin can rename, and a hint keyed on a guessed name
  // would go silent the moment they did.
  const localModelNames = useMemo(
    () => nousLlm.filter((m) => m.is_local).map((m) => m.name),
    [nousLlm],
  );

  // Platform-model self-check, surfaced next to the picker (spec
  // 2026-08-14 §F2). Only platform models carry it — a BYOK provider's models
  // are never probed, so they stay absent from the map and the UI silent.
  const modelHealth = useMemo(() => buildModelHealth(nousLlm), [nousLlm]);
  const unhealthyModelLabels = useMemo(() => {
    const out: Record<string, string> = {};
    for (const [name, health] of Object.entries(modelHealth)) {
      if (health.status !== 'fail') continue;
      const checked = health.testedAt
        ? t('aiLibrary.agents.modelHealthCheckedAgo', 'checked {{ago}}', {
            ago: formatRelativeTime(health.testedAt, t),
          })
        : '';
      // The reason is a closed enum from the backend (mig 427), so it is safe
      // to render inside the picker; the probe's raw text never leaves admin.
      // A missing/unknown code degrades to the reason-less wording.
      const reasonKey = healthReasonKey(health.code);
      const failed = reasonKey
        ? t('aiLibrary.agents.modelHealthFailedReasonShort', 'health check failed: {{reason}}', {
            reason: t(reasonKey),
          })
        : t('aiLibrary.agents.modelHealthFailedShort', 'health check failed');
      out[name] = checked ? `${failed}, ${checked}` : failed;
    }
    return out;
  }, [modelHealth, t]);
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
  const [permReason, setPermReason] = useState('');
  // null = not yet successfully fetched (either still loading or this tab
  // was never opened). Distinct from `[]`, which means "fetched, zero rows".
  const [permAudits, setPermAudits] = useState<AgentPermissionAudit[] | null>(null);
  const [permAuditsLoading, setPermAuditsLoading] = useState(false);
  // Flips permanently on a fetch failure (403 for non-owners, or any other
  // error) — the change-log group is then never rendered, silently, per the
  // "don't surface an error UI for a read-only side panel" call.
  const [permAuditsHidden, setPermAuditsHidden] = useState(false);
  // Declared with the other hooks on purpose — see the file-header WARNING.
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forkModalOpen, setForkModalOpen] = useState(false);
  const [allAgents, setAllAgents] = useState<AILibraryAgent[]>([]);

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

  // Lazily load the permission audit trail the first time the Permissions
  // tab is opened — same shape as the skills effect above. `permAuditsHidden`
  // stops it from retrying after a 403: the caller isn't going to gain
  // access to the audit endpoint just because they switched tabs again.
  useEffect(() => {
    if (sub !== 'permissions' || permAudits !== null || permAuditsHidden) return;
    let cancelled = false;
    setPermAuditsLoading(true);
    aiLibraryService
      .getPermissionAudits(slug)
      .then((res) => {
        if (cancelled) return;
        setPermAudits(res.items);
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[AgentEditor] getPermissionAudits failed:', err);
        setPermAuditsHidden(true);
      })
      .finally(() => {
        if (!cancelled) setPermAuditsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sub, permAudits, permAuditsHidden, slug]);

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
   * 复位 (mig 341): drop the caller's personal override layer so this system
   * preset falls back to the admin/system defaults. The endpoint returns the
   * refreshed MERGED agent, so the draft is re-seeded from the response
   * rather than from a second GET — the reset and what the form shows can't
   * drift apart.
   *
   * scope='user' (the service default) is the only reachable layer here: the
   * single-agent GET never merges a team override, so nothing the editor
   * displays can have come from one.
   */
  const handleResetOverride = async (): Promise<void> => {
    if (resetting) return;
    if (!window.confirm(
      t('aiLibrary.agents.overrideResetConfirm',
        'Discard your personal changes and restore this system template? Your edits cannot be recovered.'),
    )) return;
    setResetting(true);
    try {
      const updated = await aiLibraryService.deleteAgentOverride(slug);
      setAgent(updated);
      setDraft(buildDraft(updated));
      addToast(
        t('aiLibrary.agents.overrideResetToast', 'Restored system defaults'),
        'success',
      );
      // The sidebar keeps its own list, whose "customized" badge reads
      // override_scopes — this agent just left that set.
      window.dispatchEvent(new CustomEvent('ai-library:agents-changed'));
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
        permission_change_reason: permReason.trim() || undefined,
      });
      setAgent(updated);
      setPermDraft(updated.chat_permissions ?? {});
      setCapsDraft(updated.capabilities ?? {});
      setPermReason('');
      addToast(t('aiLibrary.agents.saved', 'Agent saved'), 'success');
      // Refetch the audit trail — a PATCH that resolves to the identical
      // permission state (including a reason-only edit with nothing else
      // changed) writes neither the profile nor an audit row, so no new row
      // showing up here is the expected, non-error outcome.
      if (!permAuditsHidden) {
        try {
          const auditRes = await aiLibraryService.getPermissionAudits(agent.slug);
          setPermAudits(auditRes.items);
        } catch (auditErr) {
          console.error('[AgentEditor] refetch getPermissionAudits failed:', auditErr);
          setPermAuditsHidden(true);
        }
      }
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
        {/* Three groups, weakest first, separated by a gap twice the size of
            the one inside a group: [state + overflow] · [secondary] · [primary].
            Everything used to sit in one flat gap-2 row — Assign Task, Pause,
            the status chip, "...", Save and Start chat all reading as peers,
            with Save appearing and disappearing in the middle of the row and
            shuffling its neighbours sideways. */}
        <div className="ml-auto flex items-center gap-4">
          {/* State and low-frequency menu items: read, or reach for rarely. */}
          <AgentActionBar
            agent={agent}
            readOnly={catalogLocked}
            onAgentUpdated={(updated) => {
              setAgent(updated);
              setDraft(buildDraft(updated));
            }}
            onDuplicate={openForkModal}
            onDelete={isPreset ? undefined : handleDeleteAgent}
            onResetOverride={handleResetOverride}
          />

          <div className="flex items-center gap-2">
            {/* Save only exists on the tab that owns a draft. Permissions have
                their own Save (separate endpoint, separate role gate);
                Workbench, Cost and Profile's version list edit nothing through
                this button — Profile's budget fields are the exception and are
                covered by Persona's save of the same draft object. */}
            {sub === 'persona' && (
              <button
                onClick={save}
                disabled={saving}
                data-testid="agent-save-changes"
                className="rounded-lg border border-ink-700 bg-ink-800 px-4 py-2 text-sm font-medium text-ink-200 hover:bg-ink-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
              >
                {saving ? t('common.saving') : t('aiLibrary.agents.saveChanges')}
              </button>
            )}
            {/* The one thing you came here to do, and the only filled button. */}
            <button
              type="button"
              onClick={() => requestChat(agent.slug)}
              data-testid="agent-start-chat"
              className="rounded-lg bg-ok px-5 py-2 text-sm font-semibold text-white transition-colors hover:opacity-90 whitespace-nowrap"
            >
              {t('aiLibrary.agents.startChat', 'Start chat')}
            </button>
          </div>
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
          localModelNames={localModelNames}
          modelHealth={modelHealth}
          unhealthyModelLabels={unhealthyModelLabels}
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

          <div className="mt-6">
            <label htmlFor="perm-change-reason" className="block text-sm font-medium text-content">
              {t('aiLibrary.permissions.changeReasonLabel')}
            </label>
            <input
              id="perm-change-reason"
              type="text"
              value={permReason}
              onChange={(e) => setPermReason(e.target.value)}
              placeholder={t('aiLibrary.permissions.changeReasonPlaceholder')}
              maxLength={500}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm text-content"
            />
          </div>

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

          {!permAuditsHidden && (
            <PermissionChangeLog audits={permAudits ?? []} loading={permAuditsLoading} />
          )}
        </section>
      )}

      {sub === 'cost' && <AgentCostTab slug={slug} />}

      {sub === 'profile' && (
        <AgentProfileTab
          agent={agent}
          slug={slug}
          draft={draft}
          updateDraft={updateDraft}
          catalogLocked={catalogLocked}
          onSave={save}
          saving={saving}
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


export default AgentEditor;
