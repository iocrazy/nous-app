import React, { useState, useEffect } from 'react';
import { Loader2, UserPlus, Trash2, LogOut, Shield, User, Crown, AlertTriangle, BarChart3 } from 'lucide-react';
import { TeamMember } from '../types';
import { fetchTeamMembers, updateTeam, updateMemberRole, removeMember, deleteTeam, leaveTeam } from '../services/teamService';
import { fetchUsageStats, fetchPointsBalance } from '../services/pointsService';

interface TeamSettingsProps {
  teamId: string;
  teamName: string;
  isOwner: boolean;
  currentUserId: string;
  currentUserName: string;
  currentUserEmail: string;
  onOpenInviteModal: () => void;
  onTeamDeleted: () => void;
  onTeamLeft: () => void;
}

// Per-member usage info returned from usage stats
interface MemberUsage {
  user_id: string;
  name: string | null;
  email: string | null;
  points_used: number;
  monthly_limit: number | null; // null = unlimited
}

export const TeamSettings: React.FC<TeamSettingsProps> = ({
  teamId,
  teamName,
  isOwner,
  currentUserId,
  currentUserName,
  currentUserEmail,
  onOpenInviteModal,
  onTeamDeleted,
  onTeamLeft,
}) => {
  const [name, setName] = useState(teamName);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Delete confirmation
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteConfirmText, setDeleteConfirmText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  // Leave confirmation
  const [showLeaveConfirm, setShowLeaveConfirm] = useState(false);
  const [isLeaving, setIsLeaving] = useState(false);

  // Role update loading state
  const [updatingRoleFor, setUpdatingRoleFor] = useState<string | null>(null);

  // Team consumption stats
  const [usageStats, setUsageStats] = useState<any>(null);
  const [teamBalance, setTeamBalance] = useState<number | null>(null);
  const [memberUsages, setMemberUsages] = useState<MemberUsage[]>([]);
  const [isLoadingStats, setIsLoadingStats] = useState(false);

  // Determine if current user is owner or admin
  const currentMember = members.find(m => m.user_id === currentUserId);
  const isAdmin = currentMember?.role === 'admin';
  const canViewStats = isOwner || isAdmin;

  useEffect(() => {
    loadMembers();
  }, [teamId]);

  useEffect(() => {
    if (canViewStats && teamId) {
      loadConsumptionStats();
    }
  }, [canViewStats, teamId]);

  const loadMembers = async () => {
    setIsLoading(true);
    try {
      const data = await fetchTeamMembers(teamId);
      setMembers(data);
    } catch (err) {
      setError('Failed to load team members');
    } finally {
      setIsLoading(false);
    }
  };

  const loadConsumptionStats = async () => {
    setIsLoadingStats(true);
    try {
      const [stats, balance] = await Promise.all([
        fetchUsageStats(teamId).catch(() => null),
        fetchPointsBalance(teamId).catch(() => null),
      ]);
      setUsageStats(stats);
      setTeamBalance(balance?.points_balance ?? null);

      // Extract per-member usage from stats if available
      if (stats?.member_usage && Array.isArray(stats.member_usage)) {
        setMemberUsages(stats.member_usage);
      }
    } catch {
      // Stats are non-critical, fail silently
    } finally {
      setIsLoadingStats(false);
    }
  };

  const handleSaveName = async () => {
    if (name === teamName) return;
    setIsSaving(true);
    try {
      await updateTeam(teamId, { name });
    } catch (err) {
      setError('Failed to update team name');
    } finally {
      setIsSaving(false);
    }
  };

  const handleRoleChange = async (userId: string, newRole: 'admin' | 'member') => {
    setUpdatingRoleFor(userId);
    try {
      await updateMemberRole(teamId, userId, newRole);
      setMembers(members.map(m =>
        m.user_id === userId ? { ...m, role: newRole } : m
      ));
    } catch (err) {
      setError('Failed to update member role');
    } finally {
      setUpdatingRoleFor(null);
    }
  };

  const handleRemoveMember = async (userId: string) => {
    if (!confirm('Remove this member from the team?')) return;
    try {
      await removeMember(teamId, userId);
      setMembers(members.filter(m => m.user_id !== userId));
    } catch (err) {
      setError('Failed to remove member');
    }
  };

  const handleDeleteTeam = async () => {
    if (deleteConfirmText !== teamName) return;
    setIsDeleting(true);
    try {
      await deleteTeam(teamId);
      onTeamDeleted();
    } catch (err) {
      setError('Failed to delete team');
    } finally {
      setIsDeleting(false);
    }
  };

  const handleLeaveTeam = async () => {
    setIsLeaving(true);
    try {
      await leaveTeam(teamId);
      onTeamLeft();
    } catch (err) {
      setError('Failed to leave team');
    } finally {
      setIsLeaving(false);
    }
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

  // Get display info for a member - use current user's info if it's the current user
  const getMemberDisplayInfo = (member: TeamMember) => {
    const isCurrentUser = member.user_id === currentUserId;
    const name = isCurrentUser ? currentUserName : (member.name || null);
    const email = isCurrentUser ? currentUserEmail : (member.email || null);
    return { name, email, isCurrentUser };
  };

  // Get member monthly usage info
  const getMemberUsage = (userId: string): MemberUsage | undefined => {
    return memberUsages.find(u => u.user_id === userId);
  };

  return (
    <div className="space-y-8">
      {/* Team Name */}
      {isOwner && (
        <div className="space-y-2">
          <label className="text-sm font-medium text-zinc-400">Team Name</label>
          <div className="flex gap-3">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="flex-1 bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 focus:outline-none focus:border-indigo-500"
            />
            <button
              onClick={handleSaveName}
              disabled={isSaving || name === teamName}
              className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg font-medium transition-colors"
            >
              {isSaving ? <Loader2 size={16} className="animate-spin" /> : 'Save'}
            </button>
          </div>
        </div>
      )}

      {/* Team Consumption Stats (owner and admin only) */}
      {canViewStats && (
        <div className="space-y-4">
          <h3 className="text-lg font-semibold text-white flex items-center gap-2">
            <BarChart3 size={18} />
            Team Consumption
          </h3>
          <div className="border border-zinc-800 rounded-lg p-4 bg-zinc-900/30">
            {isLoadingStats ? (
              <div className="flex items-center justify-center py-6">
                <Loader2 className="animate-spin text-zinc-500" size={20} />
              </div>
            ) : (
              <div className="space-y-4">
                {/* Summary row */}
                <div className="flex items-center gap-6">
                  <div>
                    <p className="text-xs text-zinc-500 uppercase font-medium">Points Used This Month</p>
                    <p className="text-2xl font-bold text-white">
                      {usageStats?.total_consumed_this_month?.toLocaleString() ?? '0'}
                    </p>
                  </div>
                  {teamBalance !== null && (
                    <div>
                      <p className="text-xs text-zinc-500 uppercase font-medium">Current Balance</p>
                      <p className="text-2xl font-bold text-indigo-400">
                        {teamBalance.toLocaleString()}
                      </p>
                    </div>
                  )}
                </div>

                {/* Top consumers */}
                {usageStats?.top_consumers && usageStats.top_consumers.length > 0 && (
                  <div>
                    <p className="text-xs text-zinc-500 uppercase font-medium mb-2">Top Consumers</p>
                    <div className="space-y-1.5">
                      {usageStats.top_consumers.map((consumer: any, idx: number) => (
                        <div key={consumer.user_id || idx} className="flex items-center justify-between text-sm">
                          <span className="text-zinc-300">
                            {consumer.name || consumer.email || 'Unknown'}
                          </span>
                          <span className="text-zinc-400 font-mono">
                            {consumer.points_used?.toLocaleString() ?? 0} pts
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {!usageStats && (
                  <p className="text-sm text-zinc-500">No consumption data available yet.</p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Members Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-lg font-semibold text-white">Members</h3>
          {isOwner && (
            <button
              onClick={onOpenInviteModal}
              className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-medium transition-colors"
            >
              <UserPlus size={16} />
              Invite Members
            </button>
          )}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="animate-spin text-zinc-500" size={24} />
          </div>
        ) : (
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
                {members.map((member) => {
                  const { name, email, isCurrentUser } = getMemberDisplayInfo(member);
                  const usage = getMemberUsage(member.user_id);
                  return (
                  <tr key={member.user_id} className="hover:bg-zinc-800/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                          <span className="text-xs font-bold text-white">
                            {(name || email || 'U').charAt(0).toUpperCase()}
                          </span>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-zinc-200 flex items-center gap-2">
                            <span>{name || email || 'Unknown'}</span>
                            {getRoleBadge(member.role)}
                            {isCurrentUser && (
                              <span className="text-xs text-zinc-500">(You)</span>
                            )}
                          </p>
                          {email && name && (
                            <p className="text-xs text-zinc-500">{email}</p>
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
                {members.length === 0 && (
                  <tr>
                    <td colSpan={isOwner ? (canViewStats ? 5 : 4) : (canViewStats ? 4 : 3)} className="px-4 py-8 text-center text-zinc-500">
                      No members found
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Danger Zone */}
      <div className="border-t border-zinc-800 pt-8">
        <h3 className="text-lg font-semibold text-red-400 mb-4 flex items-center gap-2">
          <AlertTriangle size={18} />
          Danger Zone
        </h3>

        {isOwner ? (
          // Delete Team
          <div className="p-4 border border-red-500/20 bg-red-500/5 rounded-lg">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="font-medium text-zinc-200">Delete Team</h4>
                <p className="text-sm text-zinc-500">
                  Permanently delete this team and all its data
                </p>
              </div>
              <button
                onClick={() => setShowDeleteConfirm(true)}
                className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg font-medium transition-colors"
              >
                Delete Team
              </button>
            </div>

            {showDeleteConfirm && (
              <div className="mt-4 p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
                <p className="text-sm text-zinc-300 mb-3">
                  Type <strong className="text-white">{teamName}</strong> to confirm:
                </p>
                <input
                  type="text"
                  value={deleteConfirmText}
                  onChange={(e) => setDeleteConfirmText(e.target.value)}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-2 text-zinc-200 focus:outline-none focus:border-red-500 mb-3"
                />
                <div className="flex gap-3">
                  <button
                    onClick={handleDeleteTeam}
                    disabled={deleteConfirmText !== teamName || isDeleting}
                    className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:bg-zinc-800 disabled:text-zinc-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                  >
                    {isDeleting && <Loader2 size={14} className="animate-spin" />}
                    Delete Forever
                  </button>
                  <button
                    onClick={() => {
                      setShowDeleteConfirm(false);
                      setDeleteConfirmText('');
                    }}
                    className="px-4 py-2 text-zinc-400 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          // Leave Team
          <div className="p-4 border border-zinc-800 bg-zinc-900/50 rounded-lg">
            <div className="flex items-center justify-between">
              <div>
                <h4 className="font-medium text-zinc-200">Leave Team</h4>
                <p className="text-sm text-zinc-500">
                  Remove yourself from this team
                </p>
              </div>
              <button
                onClick={() => setShowLeaveConfirm(true)}
                className="px-4 py-2 border border-red-500/50 text-red-400 hover:bg-red-500/10 rounded-lg font-medium transition-colors flex items-center gap-2"
              >
                <LogOut size={16} />
                Leave Team
              </button>
            </div>

            {showLeaveConfirm && (
              <div className="mt-4 p-4 bg-zinc-950 border border-zinc-800 rounded-lg">
                <p className="text-sm text-zinc-300 mb-4">
                  Are you sure you want to leave <strong>{teamName}</strong>?
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={handleLeaveTeam}
                    disabled={isLeaving}
                    className="px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                  >
                    {isLeaving && <Loader2 size={14} className="animate-spin" />}
                    Leave Team
                  </button>
                  <button
                    onClick={() => setShowLeaveConfirm(false)}
                    className="px-4 py-2 text-zinc-400 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="p-3 bg-red-500/10 border border-red-500/20 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}
    </div>
  );
};
