import React, { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X, UserPlus, Loader2, Crown, Pencil, Eye, Trash2 } from 'lucide-react';
import { Project, ProjectMember } from '../types';
import { fetchProjectMembers, addProjectMember, updateMemberRole, removeProjectMember } from '../services/projectsService';

interface ProjectMembersPanelProps {
  project: Project;
  isOpen: boolean;
  onClose: () => void;
}

const ROLE_CONFIG: Record<string, { icon: React.ReactNode; label: string; color: string }> = {
  admin: { icon: <Crown size={12} />, label: 'Admin', color: 'text-amber-400 bg-amber-500/10' },
  editor: { icon: <Pencil size={12} />, label: 'Editor', color: 'text-blue-400 bg-blue-500/10' },
  viewer: { icon: <Eye size={12} />, label: 'Viewer', color: 'text-ink-400 bg-ink-500/10' },
};

export const ProjectMembersPanel: React.FC<ProjectMembersPanelProps> = ({
  project, isOpen, onClose
}) => {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<string>('viewer');
  const [isInviting, setIsInviting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) loadMembers();
  }, [isOpen, project.id]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    if (isOpen) document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen, onClose]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  const loadMembers = async () => {
    setIsLoading(true);
    try {
      const data = await fetchProjectMembers(project.id);
      setMembers(data);
    } catch (err) {
      console.error('Failed to load members:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleInvite = async () => {
    if (!inviteEmail.trim()) return;
    setIsInviting(true);
    setError(null);
    try {
      // For now we use email as user_id placeholder — the backend needs to resolve
      // In a real scenario we'd search users by email first
      await addProjectMember(project.id, inviteEmail.trim(), inviteRole);
      setInviteEmail('');
      await loadMembers();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to invite member');
    } finally {
      setIsInviting(false);
    }
  };

  const handleRoleChange = async (memberId: string, newRole: string) => {
    try {
      await updateMemberRole(project.id, memberId, newRole);
      await loadMembers();
    } catch (err) {
      console.error('Failed to update role:', err);
    }
  };

  const handleRemove = async (memberId: string) => {
    try {
      await removeProjectMember(project.id, memberId);
      await loadMembers();
    } catch (err) {
      console.error('Failed to remove member:', err);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40" />

      {/* Panel */}
      <div
        ref={panelRef}
        className="relative w-96 bg-ink-900 border-l border-ink-800 h-full overflow-y-auto animate-in slide-in-from-right duration-200"
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-ink-800">
          <h2 className="text-lg font-semibold text-ink-50">
            {t('projects.members.title', 'Members')}
          </h2>
          <button
            onClick={onClose}
            className="p-1.5 text-ink-400 hover:text-ink-50 hover:bg-ink-800 rounded-lg transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <div className="p-5 space-y-6">
          {/* Owner */}
          <div>
            <label className="block text-xs font-medium text-ink-500 uppercase tracking-wider mb-2">
              {t('projects.members.owner', 'Owner')}
            </label>
            <div className="flex items-center gap-3 px-3 py-2.5 bg-ink-800/50 rounded-lg">
              <div className="w-8 h-8 rounded-full bg-indigo-500/30 flex items-center justify-center text-xs font-bold text-indigo-300">
                {project.owner_id.slice(0, 2).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm text-ink-200 truncate">{project.owner_id}</p>
                <p className="text-xs text-ink-500">{t('projects.members.ownerRole', 'Project Owner')}</p>
              </div>
            </div>
          </div>

          {/* Invite */}
          <div>
            <label className="block text-xs font-medium text-ink-500 uppercase tracking-wider mb-2">
              {t('projects.members.invite', 'Invite Member')}
            </label>
            <div className="flex gap-2">
              <input
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                placeholder={t('projects.members.userIdPlaceholder', 'User ID')}
                className="flex-1 px-3 py-2 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-200 placeholder-ink-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                onKeyDown={(e) => e.key === 'Enter' && handleInvite()}
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="px-2 py-2 bg-ink-800 border border-ink-700 rounded-lg text-sm text-ink-300 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="viewer">{t('projects.members.roleViewer', 'Viewer')}</option>
                <option value="editor">{t('projects.members.roleEditor', 'Editor')}</option>
                <option value="admin">{t('projects.members.roleAdmin', 'Admin')}</option>
              </select>
            </div>
            <button
              onClick={handleInvite}
              disabled={isInviting || !inviteEmail.trim()}
              className="mt-2 w-full py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-ink-700 disabled:text-ink-500 text-white rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2"
            >
              {isInviting ? <Loader2 size={14} className="animate-spin" /> : <UserPlus size={14} />}
              {t('projects.members.addMember', 'Add Member')}
            </button>
            {error && (
              <p className="mt-2 text-xs text-red-400">{error}</p>
            )}
          </div>

          {/* Members List */}
          <div>
            <label className="block text-xs font-medium text-ink-500 uppercase tracking-wider mb-2">
              {t('projects.members.memberList', 'Members')} ({members.length})
            </label>
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 size={20} className="animate-spin text-ink-500" />
              </div>
            ) : members.length === 0 ? (
              <p className="text-sm text-ink-500 py-4 text-center">
                {t('projects.members.noMembers', 'No members yet')}
              </p>
            ) : (
              <div className="space-y-2">
                {members.map((member) => {
                  const roleConfig = ROLE_CONFIG[member.role] || ROLE_CONFIG.viewer;
                  return (
                    <div
                      key={member.id}
                      className="flex items-center gap-3 px-3 py-2.5 bg-ink-800/50 rounded-lg group"
                    >
                      <div className="w-8 h-8 rounded-full bg-ink-700 flex items-center justify-center text-xs font-bold text-ink-300">
                        {(member.email || member.user_id).slice(0, 2).toUpperCase()}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-ink-200 truncate">
                          {member.email || member.user_id}
                        </p>
                        <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full ${roleConfig.color}`}>
                          {roleConfig.icon}
                          {roleConfig.label}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <select
                          value={member.role}
                          onChange={(e) => handleRoleChange(member.id, e.target.value)}
                          className="text-xs bg-ink-700 border border-ink-600 rounded px-1.5 py-1 text-ink-300 focus:outline-none"
                        >
                          <option value="viewer">Viewer</option>
                          <option value="editor">Editor</option>
                          <option value="admin">Admin</option>
                        </select>
                        <button
                          onClick={() => handleRemove(member.id)}
                          className="p-1 text-ink-500 hover:text-red-400 transition-colors"
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
