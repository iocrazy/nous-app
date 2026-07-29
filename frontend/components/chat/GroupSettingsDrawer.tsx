import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowLeftRight,
  Bot,
  Check,
  Crown,
  Globe,
  Lock,
  LogOut,
  Pencil,
  Plus,
  Shield,
  ShieldOff,
  Trash2,
  UserMinus,
  X,
} from 'lucide-react';
import { conversationService } from '../../services/conversationService';
import { getTeamMembers } from '../../services/teamService';
import { aiLibraryService } from '../../services/aiLibraryService';
import { useToast } from '../Toast';
import type { AILibraryAgent, Channel, ConversationMember, TeamMember } from '../../types';

interface Props {
  channel: Channel;
  teamId: string;
  currentUserId: string;
  onClose: () => void;
  /** Called after rename / visibility change so the sidebar stays in sync. */
  onChannelUpdated: (ch: Channel) => void;
  /** Called after the current user left or the group was dissolved. */
  onLeftOrDissolved: () => void;
}

function initials(str: string): string {
  return str
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? '')
    .join('');
}

/**
 * Group settings panel: group info, member list with role badges +
 * owner/admin actions (set admin, transfer owner, remove), add
 * members/agents pickers, leave/dissolve footer.
 *
 * Renders BARE (no overlay, no own island chrome) — ChatPage portals it
 * into the IslandShell info island, the same slot the resource library's
 * info panel lives in, so width/splitter/reopen are shell-owned and the
 * styling matches. Mount = open: fetch runs on mount, so the parent gates
 * rendering instead of passing an `open` prop.
 *
 * Destructive actions use a two-step inline confirm (first click arms the
 * button, second click fires) instead of a blocking browser confirm().
 */
export default function GroupSettingsPanel({
  channel,
  teamId,
  currentUserId,
  onClose,
  onChannelUpdated,
  onLeftOrDissolved,
}: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [members, setMembers] = useState<ConversationMember[]>([]);
  const [teamMembers, setTeamMembers] = useState<TeamMember[]>([]);
  const [chatAgents, setChatAgents] = useState<AILibraryAgent[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(channel.name ?? '');

  /** Action key currently armed for its second (confirming) click. */
  const [arming, setArming] = useState<string | null>(null);

  // null = self not in the loaded member list (load failed, or we were
  // removed while the drawer was open) — hide all management actions and
  // the leave/dissolve footer rather than defaulting to 'member'.
  const myRole = useMemo(
    () =>
      members.find((m) => m.member_type === 'user' && m.user_id === currentUserId)
        ?.role ?? null,
    [members, currentUserId],
  );
  const isOwner = myRole === 'owner';
  const canManage = isOwner || myRole === 'admin';
  const selfInGroup = myRole !== null;

  const userMembers = useMemo(
    () => members.filter((m) => m.member_type === 'user'),
    [members],
  );
  const agentMembers = useMemo(
    () => members.filter((m) => m.member_type === 'agent'),
    [members],
  );

  const addableMembers = useMemo(() => {
    const inGroup = new Set(userMembers.map((m) => m.user_id));
    return teamMembers.filter((m) => !inGroup.has(m.user_id));
  }, [teamMembers, userMembers]);

  const addableAgents = useMemo(() => {
    const inGroup = new Set(agentMembers.map((m) => m.agent_slug));
    return chatAgents.filter((a) => !inGroup.has(a.slug));
  }, [chatAgents, agentMembers]);

  const refreshMembers = useCallback(async () => {
    const rows = await conversationService.listMembers(channel.id);
    setMembers(rows);
  }, [channel.id]);

  useEffect(() => {
    setArming(null);
    setEditingName(false);
    setNameDraft(channel.name ?? '');
    setLoading(true);

    let cancelled = false;
    Promise.all([
      conversationService.listMembers(channel.id),
      getTeamMembers(teamId),
      aiLibraryService.listAgents(),
    ])
      .then(([rows, tms, agents]) => {
        if (cancelled) return;
        setMembers(rows);
        setTeamMembers(tms);
        setChatAgents(agents.filter((a) => a.chat_permissions?.enabled === true));
      })
      .catch((err) => {
        if (cancelled) return;
        console.error('[GroupSettingsDrawer] load failed:', err);
        addToast(t('chat.groupSettings.loadError'), 'error');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [channel.id, teamId]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Run an action with busy-guard + toast-on-error + member refresh. */
  async function run(action: () => Promise<void>, errKey: string): Promise<void> {
    if (busy) return;
    setBusy(true);
    try {
      await action();
    } catch (err) {
      console.error('[GroupSettingsDrawer] action failed:', err);
      addToast(t(errKey), 'error');
    } finally {
      setArming(null);
      setBusy(false);
    }
  }

  /** Two-step confirm: first click arms, second click executes. */
  function armedClick(key: string, fire: () => void): void {
    if (arming === key) {
      fire();
    } else {
      setArming(key);
    }
  }

  const handleRename = () =>
    run(async () => {
      const trimmed = nameDraft.trim();
      if (!trimmed || trimmed === channel.name) {
        setEditingName(false);
        return;
      }
      const updated = await conversationService.updateChannel(channel.id, {
        name: trimmed,
      });
      onChannelUpdated(updated);
      setEditingName(false);
    }, 'chat.groupSettings.renameError');

  const handleVisibility = (type: 'group' | 'public') =>
    run(async () => {
      if (type === channel.type) return;
      const updated = await conversationService.updateChannel(channel.id, { type });
      onChannelUpdated(updated);
    }, 'chat.groupSettings.updateError');

  const handleAddMember = (userId: string) =>
    run(async () => {
      await conversationService.addMembers(channel.id, [userId]);
      await refreshMembers();
    }, 'chat.groupSettings.addMemberError');

  const handleRemoveMember = (userId: string) =>
    run(async () => {
      await conversationService.removeMember(channel.id, userId);
      await refreshMembers();
    }, 'chat.groupSettings.removeMemberError');

  const handleSetRole = (userId: string, role: 'admin' | 'member') =>
    run(async () => {
      await conversationService.setMemberRole(channel.id, userId, role);
      await refreshMembers();
    }, 'chat.groupSettings.roleError');

  const handleTransfer = (userId: string) =>
    run(async () => {
      await conversationService.transferOwner(channel.id, userId);
      await refreshMembers();
    }, 'chat.groupSettings.transferError');

  const handleAddAgent = (slug: string) =>
    run(async () => {
      await conversationService.addAgent(channel.id, slug);
      await refreshMembers();
    }, 'chat.groupSettings.addAgentError');

  const handleRemoveAgent = (agentId: string) =>
    run(async () => {
      await conversationService.removeAgent(channel.id, agentId);
      await refreshMembers();
    }, 'chat.groupSettings.removeAgentError');

  const handleLeave = () =>
    run(async () => {
      await conversationService.removeMember(channel.id, currentUserId);
      onLeftOrDissolved();
    }, 'chat.groupSettings.leaveError');

  const handleDissolve = () =>
    run(async () => {
      await conversationService.dissolveChannel(channel.id);
      onLeftOrDissolved();
    }, 'chat.groupSettings.dissolveError');

  function roleBadge(role: string): React.ReactNode {
    if (role !== 'owner' && role !== 'admin') return null;
    const isOwnerBadge = role === 'owner';
    return (
      <span
        className={[
          'inline-flex items-center gap-[3px] text-[10px] font-semibold px-[6px] py-[1.5px] rounded-[5px] flex-shrink-0',
          isOwnerBadge
            ? 'bg-amber-500/[.15] text-warn'
            : 'bg-[var(--accent-soft)] text-[var(--accent-text)]',
        ].join(' ')}
      >
        {isOwnerBadge ? <Crown size={9} /> : <Shield size={9} />}
        {t(isOwnerBadge ? 'chat.groupSettings.roleOwner' : 'chat.groupSettings.roleAdmin')}
      </span>
    );
  }

  function iconBtn(
    key: string,
    title: string,
    onClick: () => void,
    icon: React.ReactNode,
    danger = false,
  ): React.ReactNode {
    const armed = arming === key;
    return (
      <button
        key={key}
        type="button"
        title={title}
        disabled={busy}
        onClick={() => (danger ? armedClick(key, onClick) : onClick())}
        className={[
          'w-[24px] h-[24px] rounded-[6px] grid place-items-center transition-colors disabled:opacity-40',
          armed
            ? 'bg-red-500/20 text-red-300'
            : danger
              ? 'text-content-3 hover:text-red-300 hover:bg-red-500/10'
              : 'text-content-3 hover:text-content hover:bg-white/[.06]',
        ].join(' ')}
      >
        {armed ? <Check size={12} /> : icon}
      </button>
    );
  }

  return (
    <div className="h-full flex flex-col">
        {/* ── Header — mirrors ResourceInfoPanel's header grammar ── */}
        <div className="flex items-center justify-between p-4 border-b border-line flex-shrink-0 bg-island sticky top-0 z-10">
          <h3 className="text-sm font-semibold text-content select-none">
            {t('chat.groupSettings.title')}
          </h3>
          <button
            onClick={onClose}
            className="p-1.5 text-content-3 hover:text-content hover:bg-island-2 rounded-lg transition-colors"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 flex flex-col gap-5">
          {loading ? (
            <div className="py-10 text-center text-[13px] text-content-3">
              {t('chat.groupSettings.loading')}
            </div>
          ) : (
            <>
              {/* ── Group info ── */}
              <section>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.groupSettings.nameLabel')}
                </label>
                {editingName ? (
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={nameDraft}
                      onChange={(e) => setNameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.nativeEvent.isComposing) void handleRename();
                        if (e.key === 'Escape') setEditingName(false);
                      }}
                      autoFocus
                      className="flex-1 bg-app-bg border border-line-strong rounded-[8px] px-[10px] py-[7px] text-[13.5px] text-content outline-none focus:border-indigo-500/50"
                    />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void handleRename()}
                      className="px-3 rounded-[8px] bg-indigo-500 text-white text-[12.5px] font-semibold hover:bg-indigo-600 disabled:opacity-50"
                    >
                      {t('chat.groupSettings.save')}
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <span className="text-[14px] text-content font-[600]">
                      {channel.name ?? channel.id}
                    </span>
                    {canManage && (
                      <button
                        type="button"
                        onClick={() => {
                          setNameDraft(channel.name ?? '');
                          setEditingName(true);
                        }}
                        className="w-[22px] h-[22px] rounded-[6px] grid place-items-center text-content-3 hover:text-content hover:bg-white/[.06]"
                        title={t('chat.groupSettings.rename')}
                      >
                        <Pencil size={11} />
                      </button>
                    )}
                  </div>
                )}
              </section>

              {/* ── Visibility ── */}
              <section>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.groupSettings.visibility')}
                </label>
                <div className="flex gap-2">
                  {(['group', 'public'] as const).map((v) => {
                    const active = channel.type === v;
                    const Icon = v === 'group' ? Lock : Globe;
                    return (
                      <button
                        key={v}
                        type="button"
                        disabled={!canManage || busy}
                        onClick={() => void handleVisibility(v)}
                        className={[
                          'flex-1 flex items-center gap-2 border rounded-[9px] px-3 py-[8px] text-[12.5px] font-semibold transition-colors disabled:cursor-not-allowed',
                          active
                            ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-content'
                            : 'border-line-strong text-content-3',
                          canManage && !active ? 'hover:text-content-2' : '',
                        ].join(' ')}
                      >
                        <Icon size={13} className={active ? 'text-[var(--accent-text)]' : ''} />
                        {t(v === 'group' ? 'chat.groupSettings.private' : 'chat.groupSettings.public')}
                      </button>
                    );
                  })}
                </div>
              </section>

              {/* ── Members ── */}
              <section>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.groupSettings.members', { count: userMembers.length })}
                </label>
                <div className="flex flex-col gap-[2px]">
                  {userMembers.map((m) => {
                    const uid = m.user_id ?? '';
                    const label = m.name || m.email || uid;
                    const isSelf = uid === currentUserId;
                    return (
                      <div
                        key={uid}
                        className="group flex items-center gap-[9px] rounded-[8px] px-[8px] py-[6px] hover:bg-white/[.04]"
                      >
                        <span className="w-[26px] h-[26px] rounded-full bg-gradient-to-br from-[#475569] to-[#64748b] grid place-items-center text-[10px] font-semibold text-white flex-shrink-0">
                          {initials(label)}
                        </span>
                        <span className="text-[13px] text-content overflow-hidden text-ellipsis whitespace-nowrap">
                          {label}
                          {isSelf && (
                            <span className="text-content-4 ml-1">
                              {t('chat.groupSettings.you')}
                            </span>
                          )}
                        </span>
                        {roleBadge(m.role)}
                        {!isSelf && (
                          <div className="ml-auto hidden group-hover:flex items-center gap-[2px]">
                            {isOwner && m.role !== 'admin' && (
                              iconBtn(
                                `admin:${uid}`,
                                t('chat.groupSettings.makeAdmin'),
                                () => void handleSetRole(uid, 'admin'),
                                <Shield size={12} />,
                              )
                            )}
                            {isOwner && m.role === 'admin' && (
                              iconBtn(
                                `unadmin:${uid}`,
                                t('chat.groupSettings.revokeAdmin'),
                                () => void handleSetRole(uid, 'member'),
                                <ShieldOff size={12} />,
                              )
                            )}
                            {isOwner &&
                              iconBtn(
                                `transfer:${uid}`,
                                t('chat.groupSettings.transferOwner'),
                                () => void handleTransfer(uid),
                                <ArrowLeftRight size={12} />,
                                true,
                              )}
                            {(isOwner || (canManage && m.role === 'member')) &&
                              iconBtn(
                                `remove:${uid}`,
                                t('chat.groupSettings.removeMember'),
                                () => void handleRemoveMember(uid),
                                <UserMinus size={12} />,
                                true,
                              )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Add members — when nobody is addable, say WHY instead of
                    rendering nothing (a 1-person team otherwise sees no add
                    affordance at all and reads the feature as missing). */}
                {addableMembers.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-[6px]">
                    {addableMembers.map((m) => {
                      const label = m.name || m.email || m.user_id;
                      return (
                        <button
                          key={m.user_id}
                          type="button"
                          disabled={busy}
                          onClick={() => void handleAddMember(m.user_id)}
                          className="flex items-center gap-[5px] rounded-[16px] pl-[7px] pr-[10px] py-[3px] text-[12px] border border-dashed border-line-strong text-content-3 hover:text-content hover:border-[var(--accent-border)] transition-colors disabled:opacity-50"
                        >
                          <Plus size={11} />
                          {label}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <p className="mt-2 text-[12px] text-content-3 leading-[1.5]">
                    {t('chat.groupSettings.allMembersAdded')}
                  </p>
                )}
              </section>

              {/* ── Agents ── */}
              <section>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.groupSettings.agents', { count: agentMembers.length })}
                </label>
                {agentMembers.length === 0 && addableAgents.length === 0 ? (
                  <p className="text-[12px] text-content-3 leading-[1.5]">
                    {t('chat.groupSettings.noAgentsHint')}
                  </p>
                ) : (
                  <>
                    <div className="flex flex-col gap-[2px]">
                      {agentMembers.map((m) => {
                        const label = m.name || m.agent_slug || m.agent_id || '';
                        return (
                          <div
                            key={m.agent_id}
                            className="group flex items-center gap-[9px] rounded-[8px] px-[8px] py-[6px] hover:bg-white/[.04]"
                          >
                            <span className="w-[26px] h-[26px] rounded-full bg-gradient-to-br from-amber-400 to-amber-500 grid place-items-center flex-shrink-0">
                              <Bot size={13} className="text-[#1a1505]" />
                            </span>
                            <span className="text-[13px] text-content overflow-hidden text-ellipsis whitespace-nowrap">
                              {label}
                            </span>
                            {canManage && (
                              <div className="ml-auto hidden group-hover:flex">
                                {iconBtn(
                                  `rmagent:${m.agent_id}`,
                                  t('chat.groupSettings.removeAgent'),
                                  () => void handleRemoveAgent(m.agent_id ?? ''),
                                  <UserMinus size={12} />,
                                  true,
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                    {addableAgents.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-[6px]">
                        {addableAgents.map((a) => (
                          <button
                            key={a.slug}
                            type="button"
                            disabled={busy}
                            onClick={() => void handleAddAgent(a.slug)}
                            className="flex items-center gap-[5px] rounded-[16px] pl-[7px] pr-[10px] py-[3px] text-[12px] border border-dashed border-amber-500/40 text-content-3 hover-text-warn hover:border-amber-500/70 transition-colors disabled:opacity-50"
                          >
                            <Plus size={11} />
                            {a.name}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </section>
            </>
          )}
        </div>

        {/* ── Footer: leave / dissolve ── */}
        {!loading && selfInGroup && (
          <div className="px-4 py-[14px] border-t border-line flex-shrink-0 flex flex-col gap-2">
            {!isOwner && (
              <button
                type="button"
                disabled={busy}
                onClick={() => armedClick('leave', () => void handleLeave())}
                className={[
                  'w-full flex items-center justify-center gap-2 rounded-[9px] py-[9px] text-[13px] font-semibold border transition-colors disabled:opacity-50',
                  arming === 'leave'
                    ? 'border-red-500/60 bg-red-500/15 text-red-300'
                    : 'border-line-strong text-content-2 hover:text-red-300 hover:border-red-500/40',
                ].join(' ')}
              >
                <LogOut size={13} />
                {arming === 'leave'
                  ? t('chat.groupSettings.leaveConfirm')
                  : t('chat.groupSettings.leave')}
              </button>
            )}
            {isOwner && (
              <button
                type="button"
                disabled={busy}
                onClick={() => armedClick('dissolve', () => void handleDissolve())}
                className={[
                  'w-full flex items-center justify-center gap-2 rounded-[9px] py-[9px] text-[13px] font-semibold border transition-colors disabled:opacity-50',
                  arming === 'dissolve'
                    ? 'border-red-500/60 bg-red-500/15 text-red-300'
                    : 'border-line-strong text-content-2 hover:text-red-300 hover:border-red-500/40',
                ].join(' ')}
              >
                <Trash2 size={13} />
                {arming === 'dissolve'
                  ? t('chat.groupSettings.dissolveConfirm')
                  : t('chat.groupSettings.dissolve')}
              </button>
            )}
          </div>
        )}
    </div>
  );
}

/**
 * Overlay fallback for viewports without the island frame (the shell's
 * info-island target only mounts ≥sm): wraps the bare panel in a fixed
 * right-side sheet with a backdrop.
 */
export function GroupSettingsOverlay(props: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-[1px]"
      onClick={(e) => {
        if (e.target === e.currentTarget) props.onClose();
      }}
    >
      <div className="w-[380px] max-w-full h-full bg-island border-l border-line-strong overflow-hidden">
        <GroupSettingsPanel {...props} />
      </div>
    </div>
  );
}
