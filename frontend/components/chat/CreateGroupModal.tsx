import React, { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Globe, Lock, X } from 'lucide-react';
import { aiLibraryService } from '../../services/aiLibraryService';
import { getTeamMembers } from '../../services/teamService';
import { conversationService } from '../../services/conversationService';
import { useToast } from '../Toast';
import type { AILibraryAgent, Channel, TeamMember } from '../../types';

// ─────────────────────────────────────────────
// Props
// ─────────────────────────────────────────────

interface Props {
  teamId: string;
  open: boolean;
  onClose: () => void;
  onCreated: (channel: Channel) => void;
}

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

type Visibility = 'group' | 'public';

function initials(str: string): string {
  return str
    .split(/\s+/)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() ?? '')
    .join('');
}

// ─────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────

export default function CreateGroupModal({ teamId, open, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const { addToast } = useToast();

  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);

  const [name, setName] = useState('');
  const [visibility, setVisibility] = useState<Visibility>('group');
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [agents, setAgents] = useState<AILibraryAgent[]>([]);
  const [selectedMemberIds, setSelectedMemberIds] = useState<Set<string>>(new Set());
  const [selectedAgentSlugs, setSelectedAgentSlugs] = useState<Set<string>>(new Set());

  const nameInputRef = useRef<HTMLInputElement>(null);

  // Reset + fetch when modal opens
  useEffect(() => {
    if (!open) return;

    setName('');
    setVisibility('group');
    setSelectedMemberIds(new Set());
    setSelectedAgentSlugs(new Set());
    setLoading(true);

    let cancelled = false;

    // getTeamMembers (backend, service-role) — NOT fetchTeamMembers (direct
    // Supabase): team_members has no name column and user_profiles RLS hides
    // other members' rows, so the direct read renders every chip as a raw UUID.
    Promise.all([
      getTeamMembers(teamId),
      aiLibraryService.listAgents(),
    ])
      .then(([teamMembers, allAgents]) => {
        if (cancelled) return;
        setMembers(teamMembers);
        setAgents(allAgents.filter(a => a.chat_permissions?.enabled === true));
      })
      .catch(err => {
        if (cancelled) return;
        console.error('[CreateGroupModal] failed to load data:', err);
        addToast(t('chat.createGroup.loadError'), 'error');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    // Focus name input after mount
    const timer = setTimeout(() => nameInputRef.current?.focus(), 60);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [open, teamId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Don't render when closed
  if (!open) return null;

  // ── Toggling ────────────────────────────────

  function toggleMember(userId: string): void {
    setSelectedMemberIds(prev => {
      const next = new Set(prev);
      if (next.has(userId)) {
        next.delete(userId);
      } else {
        next.add(userId);
      }
      return next;
    });
  }

  function toggleAgent(slug: string): void {
    setSelectedAgentSlugs(prev => {
      const next = new Set(prev);
      if (next.has(slug)) {
        next.delete(slug);
      } else {
        next.add(slug);
      }
      return next;
    });
  }

  // ── Submit ──────────────────────────────────

  async function handleCreate(): Promise<void> {
    const trimmed = name.trim();
    if (!trimmed || creating || loading) return;

    setCreating(true);
    try {
      const ch = await conversationService.createChannel({
        type: visibility,
        team_id: teamId,
        name: trimmed,
        member_ids: Array.from(selectedMemberIds),
      });

      // Add each selected agent independently.
      // One failed agent must NOT abort the whole group creation.
      const selectedAgents = agents.filter(a => selectedAgentSlugs.has(a.slug));
      for (const agent of selectedAgents) {
        try {
          await conversationService.addAgent(ch.id, agent.slug);
        } catch (err) {
          console.error('[CreateGroupModal] failed to add agent:', agent.slug, err);
          addToast(t('chat.createGroup.agentAddFailed', { name: agent.name }), 'error');
        }
      }

      addToast(t('chat.createGroup.created'), 'success');
      onCreated(ch);
      onClose();
    } catch (err) {
      console.error('[CreateGroupModal] createChannel failed:', err);
      addToast(t('chat.createGroup.createError'), 'error');
    } finally {
      setCreating(false);
    }
  }

  // Allow pressing Enter in name field to submit
  function handleNameKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      void handleCreate();
    }
  }

  // Clicking the backdrop dismisses the modal
  function handleBackdropClick(e: React.MouseEvent<HTMLDivElement>): void {
    if (e.target === e.currentTarget) onClose();
  }

  // ── Render ──────────────────────────────────

  const canCreate = !creating && !loading && name.trim().length > 0;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[2px] grid place-items-center"
      onClick={handleBackdropClick}
    >
      <div className="w-[460px] bg-island border border-line-strong rounded-[18px] shadow-[0_24px_70px_rgba(0,0,0,.6)] overflow-hidden">

        {/* ── Header ── */}
        <div className="flex items-center justify-between px-5 py-[18px] border-b border-line">
          <h3 className="text-[16px] font-semibold text-content leading-none">
            {t('chat.createGroup.title')}
          </h3>
          <button
            onClick={onClose}
            className="w-[26px] h-[26px] rounded-[7px] grid place-items-center text-content-3 hover:text-content-2 hover:bg-white/[.06] transition-colors"
            aria-label="Close"
          >
            <X size={14} strokeWidth={2} />
          </button>
        </div>

        {/* ── Body ── */}
        <div className="px-5 py-[18px] flex flex-col gap-4">

          {loading ? (
            <div className="py-8 text-center text-[13px] text-content-3">
              {t('chat.createGroup.loading')}
            </div>
          ) : (
            <>
              {/* Group name */}
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.createGroup.nameLabel')}
                </label>
                <input
                  ref={nameInputRef}
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  onKeyDown={handleNameKeyDown}
                  placeholder={t('chat.createGroup.namePlaceholder')}
                  className="w-full bg-app-bg border border-line-strong rounded-[9px] px-[11px] py-[9px] text-[14px] text-content font-[inherit] outline-none focus:border-indigo-500/50 placeholder:text-content-3"
                />
              </div>

              {/* Visibility segmented control */}
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.createGroup.visibility')}
                </label>
                <div className="flex gap-2">
                  {/* Private */}
                  <button
                    type="button"
                    onClick={() => setVisibility('group')}
                    className={[
                      'flex-1 border rounded-[9px] p-[11px] cursor-pointer flex gap-[9px] items-start text-left transition-colors',
                      visibility === 'group'
                        ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
                        : 'border-line-strong hover:border-line-strong',
                    ].join(' ')}
                  >
                    <Lock
                      size={16}
                      strokeWidth={2}
                      className={[
                        'mt-[1px] shrink-0',
                        visibility === 'group' ? 'text-[var(--accent-text)]' : 'text-content-3',
                      ].join(' ')}
                    />
                    <div>
                      <div className="text-[13px] font-semibold text-content">
                        {t('chat.createGroup.private')}
                      </div>
                      <div className="text-[11px] text-content-3 mt-[2px] leading-[1.4]">
                        {t('chat.createGroup.privateDesc')}
                      </div>
                    </div>
                  </button>

                  {/* Public */}
                  <button
                    type="button"
                    onClick={() => setVisibility('public')}
                    className={[
                      'flex-1 border rounded-[9px] p-[11px] cursor-pointer flex gap-[9px] items-start text-left transition-colors',
                      visibility === 'public'
                        ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
                        : 'border-line-strong hover:border-line-strong',
                    ].join(' ')}
                  >
                    <Globe
                      size={16}
                      strokeWidth={2}
                      className={[
                        'mt-[1px] shrink-0',
                        visibility === 'public' ? 'text-[var(--accent-text)]' : 'text-content-3',
                      ].join(' ')}
                    />
                    <div>
                      <div className="text-[13px] font-semibold text-content">
                        {t('chat.createGroup.public')}
                      </div>
                      <div className="text-[11px] text-content-3 mt-[2px] leading-[1.4]">
                        {t('chat.createGroup.publicDesc')}
                      </div>
                    </div>
                  </button>
                </div>
              </div>

              {/* Members picker */}
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.createGroup.members')}
                </label>
                {members.length === 0 ? (
                  <p className="text-[12px] text-content-3">
                    {t('chat.createGroup.noMembers')}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-[7px]">
                    {members.map(m => {
                      const label = m.name || m.email || m.user_id;
                      const selected = selectedMemberIds.has(m.user_id);
                      return (
                        <button
                          key={m.user_id}
                          type="button"
                          onClick={() => toggleMember(m.user_id)}
                          aria-pressed={selected}
                          className={[
                            'flex items-center gap-[7px] rounded-[20px] pl-[5px] pr-[10px] py-[4px]',
                            'text-[12.5px] border transition-colors',
                            selected
                              ? 'bg-[var(--accent-soft)] border-[var(--accent-border)] text-[var(--accent-text)]'
                              : 'bg-island-2 border-line text-content hover:border-line-strong',
                          ].join(' ')}
                        >
                          <span className="w-5 h-5 rounded-full bg-gradient-to-br from-[#475569] to-[#64748b] grid place-items-center text-[9px] font-semibold text-white shrink-0">
                            {initials(label)}
                          </span>
                          {label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Agents picker */}
              <div>
                <label className="block text-[11px] font-semibold uppercase tracking-[.06em] text-content-3 mb-[6px]">
                  {t('chat.createGroup.agents')}
                </label>
                {agents.length === 0 ? (
                  <p className="text-[12px] text-content-3">
                    {t('chat.createGroup.noAgents')}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-[7px]">
                    {agents.map(agent => {
                      const selected = selectedAgentSlugs.has(agent.slug);
                      return (
                        <button
                          key={agent.slug}
                          type="button"
                          onClick={() => toggleAgent(agent.slug)}
                          aria-pressed={selected}
                          className={[
                            'flex items-center gap-[7px] rounded-[20px] pl-[5px] pr-[10px] py-[4px]',
                            'text-[12.5px] border transition-colors',
                            selected
                              ? 'bg-amber-500/[.15] border-amber-500/50 text-amber-300'
                              : 'bg-island-2 border-line text-content hover:border-line-strong',
                          ].join(' ')}
                        >
                          <span className="w-5 h-5 rounded-full bg-gradient-to-br from-amber-400 to-amber-500 grid place-items-center text-[9px] font-semibold text-[#1a1505] shrink-0">
                            {initials(agent.name)}
                          </span>
                          {agent.name}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="flex justify-end gap-[10px] px-5 py-[14px] border-t border-line">
          <button
            type="button"
            onClick={onClose}
            disabled={creating}
            className="text-[13.5px] font-semibold px-4 py-[9px] rounded-[9px] bg-island-2 border border-line text-content-2 hover:text-content transition-colors disabled:opacity-50"
          >
            {t('chat.createGroup.cancel')}
          </button>
          <button
            type="button"
            onClick={() => void handleCreate()}
            disabled={!canCreate}
            className="text-[13.5px] font-semibold px-4 py-[9px] rounded-[9px] bg-indigo-500 text-white hover:bg-indigo-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating
              ? t('chat.createGroup.creating')
              : t('chat.createGroup.create')}
          </button>
        </div>

      </div>
    </div>
  );
}
