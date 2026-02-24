import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { UserPlus, Trash2, Shield, User, Crown, Loader2, Search, Users } from 'lucide-react';
import { TeamMember } from '../types';
import { fetchTeamMembers, updateMemberRole, removeMember } from '../services/teamService';
import { fetchUsageStats } from '../services/pointsService';
import { InviteMembersModal } from './InviteMembersModal';
import { hasPermission } from '../utils/permissions';

interface MembersViewProps {
  teamId: string;
  teamName: string;
  currentUserId: string;
  permissions: string[];
}

interface MemberUsage {
  user_id: string;
  name: string | null;
  email: string | null;
  points_used: number;
  monthly_limit: number | null;
}

export const MembersView: React.FC<MembersViewProps> = ({
  teamId,
  teamName,
  currentUserId,
  permissions,
}) => {
  const { t } = useTranslation();
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [showInviteModal, setShowInviteModal] = useState(false);
  const [memberUsages, setMemberUsages] = useState<MemberUsage[]>([]);
  const [updatingRoleFor, setUpdatingRoleFor] = useState<string | null>(null);

  const isOwner = hasPermission(permissions, 'member.manage');
  const canViewStats = hasPermission(permissions, 'billing.view');

  useEffect(() => {
    const loadData = async () => {
      setLoading(true);
      try {
        const [membersData, statsData] = await Promise.all([
          fetchTeamMembers(teamId).catch(() => []),
          fetchUsageStats(teamId).catch(() => null),
        ]);
        setMembers(membersData);
        if (statsData?.member_usage && Array.isArray(statsData.member_usage)) {
          setMemberUsages(statsData.member_usage);
        }
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, [teamId]);

  const handleRoleChange = async (userId: string, newRole: 'admin' | 'member') => {
    setUpdatingRoleFor(userId);
    try {
      await updateMemberRole(teamId, userId, newRole);
      setMembers(members.map(m => m.user_id === userId ? { ...m, role: newRole } : m));
    } catch {
      // silently fail
    } finally {
      setUpdatingRoleFor(null);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm(t('members.confirmRemove'))) return;
    try {
      await removeMember(teamId, userId);
      setMembers(members.filter(m => m.user_id !== userId));
    } catch {
      // silently fail
    }
  };

  const getMemberUsage = (userId: string): MemberUsage | undefined => {
    return memberUsages.find(u => u.user_id === userId);
  };

  const getRoleIcon = (role: string) => {
    switch (role) {
      case 'owner':
        return <Crown size={14} className="text-yellow-400" />;
      case 'admin':
        return <Shield size={14} className="text-indigo-400" />;
      default:
        return <User size={14} className="text-zinc-500" />;
    }
  };

  const getRoleBadge = (role: string) => {
    switch (role) {
      case 'owner':
        return (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-500/20 text-amber-400 border border-amber-500/30">
            Owner
          </span>
        );
      case 'admin':
        return (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-blue-500/20 text-blue-400 border border-blue-500/30">
            Admin
          </span>
        );
      default:
        return (
          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-700/50 text-zinc-400 border border-zinc-600/30">
            Member
          </span>
        );
    }
  };

  const filteredMembers = members.filter(m => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (m.name?.toLowerCase().includes(q) || m.email?.toLowerCase().includes(q));
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-zinc-400">
        <Loader2 className="animate-spin" size={24} />
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-bold text-zinc-100">{t('members.title')}</h2>
          <span className="bg-zinc-800 text-zinc-400 rounded-full px-2.5 py-0.5 text-sm">
            {members.length}
          </span>
        </div>
        {isOwner && (
          <button
            onClick={() => setShowInviteModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors"
          >
            <UserPlus size={16} />
            {t('members.inviteMembers')}
          </button>
        )}
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t('members.searchPlaceholder')}
          className="w-full bg-zinc-900 border border-zinc-800 rounded-lg pl-10 pr-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-indigo-500"
        />
      </div>

      {/* Members Table */}
      <div className="border border-zinc-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead className="bg-zinc-900/50 border-b border-zinc-800">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-zinc-500 uppercase">Member</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-zinc-500 uppercase">Role</th>
              {canViewStats && (
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-500 uppercase">Monthly Usage</th>
              )}
              <th className="px-4 py-3 text-left text-xs font-medium text-zinc-500 uppercase">Joined</th>
              {isOwner && (
                <th className="px-4 py-3 text-right text-xs font-medium text-zinc-500 uppercase">Actions</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/50">
            {filteredMembers.map((member) => {
              const isCurrentUser = member.user_id === currentUserId;
              const displayName = member.name || member.email || 'Unknown';
              const usage = getMemberUsage(member.user_id);
              return (
                <tr key={member.user_id} className="hover:bg-zinc-800/30">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                        <span className="text-xs font-bold text-white">
                          {displayName.charAt(0).toUpperCase()}
                        </span>
                      </div>
                      <div>
                        <p className="text-sm font-medium text-zinc-200 flex items-center gap-2">
                          <span>{displayName}</span>
                          {getRoleBadge(member.role)}
                          {isCurrentUser && (
                            <span className="text-xs text-zinc-500">(You)</span>
                          )}
                        </p>
                        {member.email && member.name && (
                          <p className="text-xs text-zinc-500">{member.email}</p>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    {isOwner && member.role !== 'owner' ? (
                      <div className="flex items-center gap-2">
                        {getRoleIcon(member.role)}
                        <select
                          value={member.role}
                          onChange={(e) => handleRoleChange(member.user_id, e.target.value as 'admin' | 'member')}
                          disabled={updatingRoleFor === member.user_id}
                          className="bg-zinc-800 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-300 focus:outline-none focus:border-indigo-500 cursor-pointer disabled:opacity-50"
                        >
                          <option value="member">Member</option>
                          <option value="admin">Admin</option>
                        </select>
                        {updatingRoleFor === member.user_id && (
                          <Loader2 size={12} className="animate-spin text-zinc-500" />
                        )}
                      </div>
                    ) : (
                      <span className="flex items-center gap-1.5 text-sm text-zinc-300">
                        {getRoleIcon(member.role)}
                        {member.role.charAt(0).toUpperCase() + member.role.slice(1)}
                      </span>
                    )}
                  </td>
                  {canViewStats && (
                    <td className="px-4 py-3 text-sm text-zinc-400">
                      {usage ? (
                        usage.monthly_limit !== null ? (
                          <span>
                            {usage.points_used.toLocaleString()} / {usage.monthly_limit.toLocaleString()} points used
                          </span>
                        ) : (
                          <span>
                            {usage.points_used.toLocaleString()} points used
                            <span className="ml-1 text-zinc-600">(Unlimited)</span>
                          </span>
                        )
                      ) : (
                        <span className="text-zinc-600">--</span>
                      )}
                    </td>
                  )}
                  <td className="px-4 py-3 text-sm text-zinc-500">
                    {new Date(member.joined_at).toLocaleDateString()}
                  </td>
                  {isOwner && (
                    <td className="px-4 py-3 text-right">
                      {member.role !== 'owner' && (
                        <button
                          onClick={() => handleRemoveMember(member.user_id)}
                          className="p-1.5 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 rounded transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}
            {filteredMembers.length === 0 && (
              <tr>
                <td
                  colSpan={isOwner ? (canViewStats ? 5 : 4) : (canViewStats ? 4 : 3)}
                  className="px-4 py-8 text-center text-zinc-500"
                >
                  {searchQuery ? t('members.noResults') : t('members.noMembers')}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Invite Modal */}
      <InviteMembersModal
        isOpen={showInviteModal}
        onClose={() => setShowInviteModal(false)}
        teamId={teamId}
        teamName={teamName}
      />
    </div>
  );
};
