import React, { useState, useEffect } from 'react';
import { X, Copy, Check, Loader2, Link2, Clock, Users, Trash2 } from 'lucide-react';
import { TeamInvite, ExpiryOption, createInvite, fetchInvites, deleteInvite, getInviteLink } from '../services/inviteService';

interface InviteMembersModalProps {
  isOpen: boolean;
  onClose: () => void;
  teamId: string;
  teamName: string;
}

type MaxUsesOption = 1 | 5 | 10 | 25 | 50 | 100 | null;

export const InviteMembersModal: React.FC<InviteMembersModalProps> = ({
  isOpen,
  onClose,
  teamId,
  teamName,
}) => {
  const [invites, setInvites] = useState<TeamInvite[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Link settings
  const [expiresIn, setExpiresIn] = useState<ExpiryOption>('7d');
  const [maxUses, setMaxUses] = useState<MaxUsesOption>(null);
  const [showSettings, setShowSettings] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadInvites();
    }
  }, [isOpen, teamId]);

  const loadInvites = async () => {
    setIsLoading(true);
    try {
      const data = await fetchInvites(teamId);
      setInvites(data);
    } catch (err) {
      setError('Failed to load invites');
    } finally {
      setIsLoading(false);
    }
  };

  const handleCreateInvite = async () => {
    setIsCreating(true);
    setError(null);
    try {
      const invite = await createInvite(teamId, { expiresIn, maxUses });
      setInvites([invite, ...invites]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create invite');
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteInvite = async (inviteId: string) => {
    try {
      await deleteInvite(inviteId);
      setInvites(invites.filter(i => i.id !== inviteId));
    } catch (err) {
      setError('Failed to delete invite');
    }
  };

  const handleCopyLink = (code: string, id: string) => {
    navigator.clipboard.writeText(getInviteLink(code));
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const getExpiryLabel = (expiresAt: string | null) => {
    if (!expiresAt) return 'Never';
    const date = new Date(expiresAt);
    const now = new Date();
    if (date < now) return 'Expired';
    const diff = date.getTime() - now.getTime();
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const days = Math.floor(hours / 24);
    if (days > 0) return `${days}d left`;
    if (hours > 0) return `${hours}h left`;
    const minutes = Math.floor(diff / (1000 * 60));
    if (minutes > 0) return `${minutes}m left`;
    return 'Soon';
  };

  if (!isOpen) return null;

  const expiryOptions: { value: ExpiryOption; label: string }[] = [
    { value: '30m', label: '30 minutes' },
    { value: '1h', label: '1 hour' },
    { value: '6h', label: '6 hours' },
    { value: '12h', label: '12 hours' },
    { value: '1d', label: '1 day' },
    { value: '7d', label: '7 days' },
    { value: 'never', label: 'Never' },
  ];

  const maxUsesOptions: { value: MaxUsesOption; label: string }[] = [
    { value: null, label: 'No limit' },
    { value: 1, label: '1 use' },
    { value: 5, label: '5 uses' },
    { value: 10, label: '10 uses' },
    { value: 25, label: '25 uses' },
    { value: 50, label: '50 uses' },
    { value: 100, label: '100 uses' },
  ];

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative bg-zinc-900 border border-zinc-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-zinc-800">
          <div>
            <h2 className="text-lg font-semibold text-white">Invite Members</h2>
            <p className="text-sm text-zinc-500">{teamName}</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
          >
            <X size={20} />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Create New Invite */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-zinc-300">Create a new invite link:</p>
              <button
                onClick={() => setShowSettings(!showSettings)}
                className="text-xs text-indigo-400 hover:text-indigo-300"
              >
                {showSettings ? 'Hide Settings' : 'Link Settings'}
              </button>
            </div>

            {showSettings && (
              <div className="p-4 bg-zinc-800/50 rounded-lg space-y-4 animate-in fade-in slide-in-from-top-2">
                <div className="space-y-2">
                  <label className="text-xs font-medium text-zinc-400 flex items-center gap-1">
                    <Clock size={12} />
                    Expires after
                  </label>
                  <select
                    value={expiresIn}
                    onChange={(e) => setExpiresIn(e.target.value as ExpiryOption)}
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500"
                  >
                    {expiryOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                <div className="space-y-2">
                  <label className="text-xs font-medium text-zinc-400 flex items-center gap-1">
                    <Users size={12} />
                    Max uses
                  </label>
                  <select
                    value={maxUses === null ? 'null' : maxUses.toString()}
                    onChange={(e) => setMaxUses(e.target.value === 'null' ? null : parseInt(e.target.value) as MaxUsesOption)}
                    className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-indigo-500"
                  >
                    {maxUsesOptions.map((opt) => (
                      <option key={opt.value === null ? 'null' : opt.value} value={opt.value === null ? 'null' : opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}

            <button
              onClick={handleCreateInvite}
              disabled={isCreating}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg font-medium transition-colors"
            >
              {isCreating ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Link2 size={16} />
              )}
              Generate New Link
            </button>
          </div>

          {/* Existing Invites */}
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="animate-spin text-zinc-500" size={24} />
            </div>
          ) : invites.length > 0 ? (
            <div className="space-y-3">
              <p className="text-xs font-medium text-zinc-500 uppercase tracking-wider">Active Invites</p>
              {invites.map((invite) => (
                <div
                  key={invite.id}
                  className="flex items-center gap-3 p-3 bg-zinc-800/50 rounded-lg"
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-mono text-zinc-300 truncate">
                      {getInviteLink(invite.code)}
                    </p>
                    <div className="flex items-center gap-3 mt-1 text-xs text-zinc-500">
                      <span className="flex items-center gap-1">
                        <Clock size={10} />
                        {getExpiryLabel(invite.expires_at)}
                      </span>
                      <span className="flex items-center gap-1">
                        <Users size={10} />
                        {invite.use_count}{invite.max_uses ? `/${invite.max_uses}` : ''} uses
                      </span>
                    </div>
                  </div>
                  <button
                    onClick={() => handleCopyLink(invite.code, invite.id)}
                    className={`p-2 rounded-lg transition-colors ${
                      copiedId === invite.id
                        ? 'bg-green-500/10 text-green-400'
                        : 'hover:bg-zinc-700 text-zinc-400 hover:text-white'
                    }`}
                  >
                    {copiedId === invite.id ? <Check size={16} /> : <Copy size={16} />}
                  </button>
                  <button
                    onClick={() => handleDeleteInvite(invite.id)}
                    className="p-2 hover:bg-red-500/10 text-zinc-400 hover:text-red-400 rounded-lg transition-colors"
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-center text-sm text-zinc-500 py-4">
              No active invite links
            </p>
          )}

          {error && (
            <p className="text-sm text-red-400 text-center">{error}</p>
          )}
        </div>
      </div>
    </div>
  );
};
