import React, { useState, useEffect } from 'react';
import { Loader2, UserPlus, Trash2, LogOut, Shield, User, Crown, AlertTriangle } from 'lucide-react';
import { TeamMember } from '../types';
import { fetchTeamMembers, updateTeam, removeMember, deleteTeam, leaveTeam } from '../services/teamService';

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

  useEffect(() => {
    loadMembers();
  }, [teamId]);

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

  // Get display info for a member - use current user's info if it's the current user
  const getMemberDisplayInfo = (member: TeamMember) => {
    const isCurrentUser = member.user_id === currentUserId;
    const name = isCurrentUser ? currentUserName : (member.name || null);
    const email = isCurrentUser ? currentUserEmail : (member.email || null);
    return { name, email, isCurrentUser };
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
                  <th className="px-4 py-3 text-left text-xs font-medium text-zinc-500 uppercase">Joined</th>
                  {isOwner && (
                    <th className="px-4 py-3 text-right text-xs font-medium text-zinc-500 uppercase">Actions</th>
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/50">
                {members.map((member) => {
                  const { name, email, isCurrentUser } = getMemberDisplayInfo(member);
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
                          <p className="text-sm font-medium text-zinc-200">
                            {name || email || 'Unknown'}
                            {isCurrentUser && (
                              <span className="ml-2 text-xs text-zinc-500">(You)</span>
                            )}
                          </p>
                          {email && name && (
                            <p className="text-xs text-zinc-500">{email}</p>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="flex items-center gap-1.5 text-sm text-zinc-300">
                        {getRoleIcon(member.role)}
                        {member.role.charAt(0).toUpperCase() + member.role.slice(1)}
                      </span>
                    </td>
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
                    <td colSpan={isOwner ? 4 : 3} className="px-4 py-8 text-center text-zinc-500">
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
