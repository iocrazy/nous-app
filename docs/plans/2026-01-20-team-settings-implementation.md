# Team Settings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement a full-screen Settings Modal with Personal Settings and Team Settings, including invite system and team deletion.

**Architecture:** Full-screen modal with left sidebar navigation. Personal Settings uses Supabase Auth for profile/password. Team Settings shows member table with role management. Invite system generates time-limited codes. All data persisted to Supabase.

**Tech Stack:** React 19, TypeScript, TailwindCSS, Supabase (Auth + Database), Lucide icons

---

## Phase 1: Database Migration for Team Invites

### Task 1.1: Create team_invites table migration

**Files:**
- Create: `supabase/migrations/022_create_team_invites_table.sql`

**Step 1: Write the migration SQL**

```sql
-- 022_create_team_invites_table.sql
-- Team invite links with expiration and usage limits

-- Create team_invites table
CREATE TABLE IF NOT EXISTS team_invites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  code VARCHAR(20) UNIQUE NOT NULL,
  created_by UUID REFERENCES auth.users(id),
  expires_at TIMESTAMPTZ,
  max_uses INTEGER,
  use_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS
ALTER TABLE team_invites ENABLE ROW LEVEL SECURITY;

-- Index for quick code lookup
CREATE INDEX IF NOT EXISTS idx_team_invites_code ON team_invites(code);
CREATE INDEX IF NOT EXISTS idx_team_invites_team ON team_invites(team_id);

-- RLS Policies

-- Team members can view their team's invites
CREATE POLICY "Team members can view invites"
ON team_invites FOR SELECT
USING (
  team_id IN (
    SELECT team_id FROM team_members WHERE user_id = auth.uid()
  )
);

-- Team owners/admins can create invites
CREATE POLICY "Team owners can create invites"
ON team_invites FOR INSERT
WITH CHECK (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Team owners can delete invites
CREATE POLICY "Team owners can delete invites"
ON team_invites FOR DELETE
USING (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Team owners can update invites (for use_count increment)
CREATE POLICY "Team owners can update invites"
ON team_invites FOR UPDATE
USING (
  team_id IN (
    SELECT id FROM teams WHERE owner_id = auth.uid()
  )
);

-- Function to generate unique invite code (different from team invite_code)
CREATE OR REPLACE FUNCTION generate_team_invite_code()
RETURNS TEXT AS $$
DECLARE
  chars TEXT := 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789';
  result TEXT := '';
  i INT;
BEGIN
  FOR i IN 1..12 LOOP
    result := result || substr(chars, floor(random() * length(chars) + 1)::int, 1);
  END LOOP;
  RETURN result;
END;
$$ LANGUAGE plpgsql;

-- Auto-generate invite code on insert
CREATE OR REPLACE FUNCTION set_team_invite_code()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.code IS NULL THEN
    NEW.code := generate_team_invite_code();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS team_invites_code_trigger ON team_invites;
CREATE TRIGGER team_invites_code_trigger
  BEFORE INSERT ON team_invites
  FOR EACH ROW
  EXECUTE FUNCTION set_team_invite_code();

-- Add admin role to team_members role check (update existing constraint)
ALTER TABLE team_members DROP CONSTRAINT IF EXISTS team_members_role_check;
ALTER TABLE team_members ADD CONSTRAINT team_members_role_check
  CHECK (role IN ('owner', 'admin', 'member'));
```

**Step 2: Run migration in Supabase**

Execute in Supabase SQL Editor or via MCP.

**Step 3: Commit**

```bash
git add supabase/migrations/022_create_team_invites_table.sql
git commit -m "feat: add team_invites table with expiration and usage limits"
```

---

## Phase 2: Settings Modal Shell

### Task 2.1: Create SettingsModal component

**Files:**
- Create: `frontend/components/SettingsModal.tsx`

**Step 1: Create the modal shell with sidebar navigation**

```tsx
import React, { useState, useEffect } from 'react';
import { X, User, Users, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

type SettingsTab = 'personal' | 'team';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialTab?: SettingsTab;
  currentTeamId: string | null;
  currentTeamName?: string;
  isTeamOwner?: boolean;
  user: {
    id: string;
    name: string;
    email: string;
    avatarUrl?: string;
    bio?: string;
  };
  onUserUpdated?: () => void;
  onTeamDeleted?: () => void;
  onTeamLeft?: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  initialTab = 'personal',
  currentTeamId,
  currentTeamName,
  isTeamOwner = false,
  user,
  onUserUpdated,
  onTeamDeleted,
  onTeamLeft,
}) => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);

  useEffect(() => {
    if (isOpen) {
      setActiveTab(initialTab);
    }
  }, [isOpen, initialTab]);

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    if (isOpen) {
      document.addEventListener('keydown', handleEsc);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEsc);
      document.body.style.overflow = '';
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const navItems = [
    { id: 'personal' as const, label: 'Personal Settings', icon: User },
    ...(currentTeamId ? [{ id: 'team' as const, label: 'Team Settings', icon: Users }] : []),
  ];

  return (
    <div className="fixed inset-0 z-50 bg-zinc-950">
      {/* Header */}
      <div className="h-14 border-b border-zinc-800 flex items-center justify-between px-6">
        <h1 className="text-lg font-semibold text-white">Settings</h1>
        <button
          onClick={onClose}
          className="p-2 text-zinc-400 hover:text-white hover:bg-zinc-800 rounded-lg transition-colors"
        >
          <X size={20} />
        </button>
      </div>

      <div className="flex h-[calc(100vh-56px)]">
        {/* Sidebar */}
        <div className="w-64 border-r border-zinc-800 p-4">
          <nav className="space-y-1">
            {navItems.map((item) => (
              <button
                key={item.id}
                onClick={() => setActiveTab(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  activeTab === item.id
                    ? 'bg-indigo-500/10 text-indigo-400'
                    : 'text-zinc-400 hover:text-white hover:bg-zinc-800/50'
                }`}
              >
                <item.icon size={18} />
                {item.label}
                {activeTab === item.id && (
                  <ChevronRight size={16} className="ml-auto" />
                )}
              </button>
            ))}
          </nav>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-8">
          {activeTab === 'personal' && (
            <div className="max-w-2xl">
              <h2 className="text-xl font-semibold text-white mb-6">Personal Settings</h2>
              {/* PersonalSettings component will go here */}
              <p className="text-zinc-500">Personal settings content placeholder</p>
            </div>
          )}

          {activeTab === 'team' && currentTeamId && (
            <div className="max-w-4xl">
              <h2 className="text-xl font-semibold text-white mb-6">
                Team Settings
                {currentTeamName && (
                  <span className="text-zinc-500 font-normal ml-2">- {currentTeamName}</span>
                )}
              </h2>
              {/* TeamSettings component will go here */}
              <p className="text-zinc-500">Team settings content placeholder</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
```

**Step 2: Commit**

```bash
git add frontend/components/SettingsModal.tsx
git commit -m "feat: add SettingsModal shell with sidebar navigation"
```

---

## Phase 3: Personal Settings Section

### Task 3.1: Create PersonalSettings component

**Files:**
- Create: `frontend/components/PersonalSettings.tsx`
- Modify: `frontend/components/SettingsModal.tsx`

**Step 1: Create PersonalSettings component**

```tsx
import React, { useState } from 'react';
import { Camera, Loader2, Check, AlertCircle, Eye, EyeOff } from 'lucide-react';
import { getSupabaseClient } from '../supabaseClient';

interface PersonalSettingsProps {
  user: {
    id: string;
    name: string;
    email: string;
    avatarUrl?: string;
    bio?: string;
  };
  onUserUpdated?: () => void;
}

export const PersonalSettings: React.FC<PersonalSettingsProps> = ({
  user,
  onUserUpdated,
}) => {
  const [name, setName] = useState(user.name || '');
  const [bio, setBio] = useState(user.bio || '');
  const [avatarUrl, setAvatarUrl] = useState(user.avatarUrl || '');
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Password change state
  const [showPasswordForm, setShowPasswordForm] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showCurrentPassword, setShowCurrentPassword] = useState(false);
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  const handleSaveProfile = async () => {
    setIsSaving(true);
    setError(null);
    setSaveSuccess(false);

    try {
      const supabase = getSupabaseClient();
      if (!supabase) throw new Error('Supabase not configured');

      const { error: updateError } = await supabase.auth.updateUser({
        data: {
          display_name: name,
          bio: bio,
          avatar_url: avatarUrl,
        },
      });

      if (updateError) throw updateError;

      setSaveSuccess(true);
      onUserUpdated?.();
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update profile');
    } finally {
      setIsSaving(false);
    }
  };

  const handleChangePassword = async () => {
    if (newPassword !== confirmPassword) {
      setPasswordError('Passwords do not match');
      return;
    }
    if (newPassword.length < 6) {
      setPasswordError('Password must be at least 6 characters');
      return;
    }

    setIsChangingPassword(true);
    setPasswordError(null);
    setPasswordSuccess(false);

    try {
      const supabase = getSupabaseClient();
      if (!supabase) throw new Error('Supabase not configured');

      const { error: updateError } = await supabase.auth.updateUser({
        password: newPassword,
      });

      if (updateError) throw updateError;

      setPasswordSuccess(true);
      setShowPasswordForm(false);
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setTimeout(() => setPasswordSuccess(false), 3000);
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : 'Failed to change password');
    } finally {
      setIsChangingPassword(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Avatar */}
      <div className="flex items-start gap-6">
        <div className="relative">
          <div className="w-24 h-24 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden">
            {avatarUrl ? (
              <img src={avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
            ) : (
              <span className="text-3xl font-bold text-white">
                {name.charAt(0).toUpperCase() || 'U'}
              </span>
            )}
          </div>
          <button className="absolute bottom-0 right-0 p-2 bg-zinc-800 border border-zinc-700 rounded-full hover:bg-zinc-700 transition-colors">
            <Camera size={14} className="text-zinc-300" />
          </button>
        </div>
        <div className="flex-1 space-y-1">
          <h3 className="text-sm font-medium text-zinc-400">Profile Photo</h3>
          <p className="text-xs text-zinc-500">
            Click the camera icon to upload a new photo
          </p>
          <input
            type="text"
            placeholder="Or paste image URL"
            value={avatarUrl}
            onChange={(e) => setAvatarUrl(e.target.value)}
            className="mt-2 w-full bg-zinc-900 border border-zinc-800 rounded-lg px-3 py-2 text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500"
          />
        </div>
      </div>

      {/* Username */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-zinc-400">Username</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 focus:outline-none focus:border-indigo-500 transition-colors"
        />
      </div>

      {/* Bio */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-zinc-400">Bio</label>
        <textarea
          value={bio}
          onChange={(e) => setBio(e.target.value)}
          rows={3}
          placeholder="Tell us about yourself..."
          className="w-full bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-indigo-500 transition-colors resize-none"
        />
      </div>

      {/* Email (readonly) */}
      <div className="space-y-2">
        <label className="text-sm font-medium text-zinc-400">Email</label>
        <input
          type="email"
          value={user.email}
          disabled
          className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-500 cursor-not-allowed"
        />
        <p className="text-xs text-zinc-600">Email cannot be changed</p>
      </div>

      {/* Save Profile Button */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleSaveProfile}
          disabled={isSaving}
          className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
        >
          {isSaving ? (
            <Loader2 size={16} className="animate-spin" />
          ) : saveSuccess ? (
            <Check size={16} />
          ) : null}
          {saveSuccess ? 'Saved!' : 'Save Changes'}
        </button>
        {error && (
          <span className="text-sm text-red-400 flex items-center gap-1">
            <AlertCircle size={14} />
            {error}
          </span>
        )}
      </div>

      {/* Divider */}
      <div className="border-t border-zinc-800 pt-8">
        <h3 className="text-lg font-semibold text-white mb-4">Security</h3>

        {/* Change Password */}
        {!showPasswordForm ? (
          <button
            onClick={() => setShowPasswordForm(true)}
            className="px-4 py-2.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 rounded-lg font-medium transition-colors"
          >
            Change Password
          </button>
        ) : (
          <div className="space-y-4 p-4 bg-zinc-900 border border-zinc-800 rounded-lg">
            <div className="space-y-2">
              <label className="text-sm font-medium text-zinc-400">New Password</label>
              <div className="relative">
                <input
                  type={showNewPassword ? 'text' : 'password'}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 pr-10 text-zinc-200 focus:outline-none focus:border-indigo-500"
                />
                <button
                  type="button"
                  onClick={() => setShowNewPassword(!showNewPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
                >
                  {showNewPassword ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-zinc-400">Confirm Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className="w-full bg-zinc-950 border border-zinc-800 rounded-lg px-4 py-3 text-zinc-200 focus:outline-none focus:border-indigo-500"
              />
            </div>

            {passwordError && (
              <p className="text-sm text-red-400 flex items-center gap-1">
                <AlertCircle size={14} />
                {passwordError}
              </p>
            )}

            {passwordSuccess && (
              <p className="text-sm text-green-400 flex items-center gap-1">
                <Check size={14} />
                Password changed successfully
              </p>
            )}

            <div className="flex gap-3">
              <button
                onClick={handleChangePassword}
                disabled={isChangingPassword}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
              >
                {isChangingPassword && <Loader2 size={14} className="animate-spin" />}
                Update Password
              </button>
              <button
                onClick={() => {
                  setShowPasswordForm(false);
                  setPasswordError(null);
                  setNewPassword('');
                  setConfirmPassword('');
                }}
                className="px-4 py-2 text-zinc-400 hover:text-white transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
```

**Step 2: Import PersonalSettings in SettingsModal**

In `SettingsModal.tsx`, add:

```tsx
import { PersonalSettings } from './PersonalSettings';

// In the content area, replace placeholder:
{activeTab === 'personal' && (
  <div className="max-w-2xl">
    <h2 className="text-xl font-semibold text-white mb-6">Personal Settings</h2>
    <PersonalSettings user={user} onUserUpdated={onUserUpdated} />
  </div>
)}
```

**Step 3: Commit**

```bash
git add frontend/components/PersonalSettings.tsx frontend/components/SettingsModal.tsx
git commit -m "feat: add PersonalSettings with profile editing and password change"
```

---

## Phase 4: Team Settings Section

### Task 4.1: Create TeamSettings component with members table

**Files:**
- Create: `frontend/components/TeamSettings.tsx`
- Modify: `frontend/components/SettingsModal.tsx`
- Modify: `frontend/services/teamService.ts`

**Step 1: Add new service functions to teamService.ts**

Add to `frontend/services/teamService.ts`:

```typescript
export const updateTeam = async (teamId: string, updates: { name?: string; description?: string }): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data, error } = await supabase
    .from('teams')
    .update(updates)
    .eq('id', teamId)
    .select()
    .single();

  if (error) throw error;
  return data;
};

export const updateMemberRole = async (
  teamId: string,
  userId: string,
  role: 'admin' | 'member'
): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_members')
    .update({ role })
    .eq('team_id', teamId)
    .eq('user_id', userId);

  if (error) throw error;
};

export const removeMember = async (teamId: string, userId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_members')
    .delete()
    .eq('team_id', teamId)
    .eq('user_id', userId);

  if (error) throw error;
};

// Fetch members with user info via join
export const fetchTeamMembersWithInfo = async (teamId: string): Promise<TeamMember[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  // First get team members
  const { data: members, error: membersError } = await supabase
    .from('team_members')
    .select('*')
    .eq('team_id', teamId)
    .order('joined_at', { ascending: true });

  if (membersError) throw membersError;
  if (!members?.length) return [];

  // For each member, we'd normally join with auth.users but RLS prevents direct access
  // The user info should come from user_metadata in auth.users
  // For now, return members as-is (user info will be fetched separately if needed)
  return members;
};
```

**Step 2: Create TeamSettings component**

```tsx
import React, { useState, useEffect } from 'react';
import { Loader2, UserPlus, Trash2, LogOut, Shield, User, Crown, AlertTriangle } from 'lucide-react';
import { TeamMember } from '../types';
import { fetchTeamMembersWithInfo, updateTeam, removeMember, deleteTeam, leaveTeam } from '../services/teamService';

interface TeamSettingsProps {
  teamId: string;
  teamName: string;
  isOwner: boolean;
  currentUserId: string;
  onOpenInviteModal: () => void;
  onTeamDeleted: () => void;
  onTeamLeft: () => void;
}

export const TeamSettings: React.FC<TeamSettingsProps> = ({
  teamId,
  teamName,
  isOwner,
  currentUserId,
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
      const data = await fetchTeamMembersWithInfo(teamId);
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
                {members.map((member) => (
                  <tr key={member.user_id} className="hover:bg-zinc-800/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
                          <span className="text-xs font-bold text-white">
                            {(member.email || member.name || 'U').charAt(0).toUpperCase()}
                          </span>
                        </div>
                        <div>
                          <p className="text-sm font-medium text-zinc-200">
                            {member.name || 'Unknown'}
                            {member.user_id === currentUserId && (
                              <span className="ml-2 text-xs text-zinc-500">(You)</span>
                            )}
                          </p>
                          <p className="text-xs text-zinc-500">{member.email || ''}</p>
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
                ))}
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
```

**Step 3: Commit**

```bash
git add frontend/components/TeamSettings.tsx frontend/services/teamService.ts
git commit -m "feat: add TeamSettings with members table and danger zone"
```

---

## Phase 5: Invite Members Modal

### Task 5.1: Create invite service functions

**Files:**
- Create: `frontend/services/inviteService.ts`

**Step 1: Create invite service**

```typescript
import { getSupabaseClient } from '../supabaseClient';

export interface TeamInvite {
  id: string;
  team_id: string;
  code: string;
  created_by: string;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  created_at: string;
}

export const createInvite = async (
  teamId: string,
  options: {
    expiresIn?: '30m' | '1h' | '6h' | '12h' | '1d' | '7d' | 'never';
    maxUses?: number | null;
  } = {}
): Promise<TeamInvite> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  let expires_at: string | null = null;
  if (options.expiresIn && options.expiresIn !== 'never') {
    const now = new Date();
    const expiryMap: Record<string, number> = {
      '30m': 30 * 60 * 1000,
      '1h': 60 * 60 * 1000,
      '6h': 6 * 60 * 60 * 1000,
      '12h': 12 * 60 * 60 * 1000,
      '1d': 24 * 60 * 60 * 1000,
      '7d': 7 * 24 * 60 * 60 * 1000,
    };
    expires_at = new Date(now.getTime() + expiryMap[options.expiresIn]).toISOString();
  }

  const { data, error } = await supabase
    .from('team_invites')
    .insert({
      team_id: teamId,
      created_by: user.id,
      expires_at,
      max_uses: options.maxUses || null,
    })
    .select()
    .single();

  if (error) throw error;
  return data;
};

export const fetchInvites = async (teamId: string): Promise<TeamInvite[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('team_invites')
    .select('*')
    .eq('team_id', teamId)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
};

export const deleteInvite = async (inviteId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_invites')
    .delete()
    .eq('id', inviteId);

  if (error) throw error;
};

export const acceptInvite = async (code: string): Promise<{ teamId: string; teamName: string }> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  // Find the invite
  const { data: invite, error: inviteError } = await supabase
    .from('team_invites')
    .select('*, teams(id, name)')
    .eq('code', code)
    .single();

  if (inviteError || !invite) throw new Error('Invalid invite code');

  // Check expiration
  if (invite.expires_at && new Date(invite.expires_at) < new Date()) {
    throw new Error('Invite has expired');
  }

  // Check max uses
  if (invite.max_uses && invite.use_count >= invite.max_uses) {
    throw new Error('Invite has reached max uses');
  }

  // Add user to team
  const { error: memberError } = await supabase
    .from('team_members')
    .insert({ team_id: invite.team_id, user_id: user.id, role: 'member' });

  if (memberError) {
    if (memberError.code === '23505') throw new Error('Already a member');
    throw memberError;
  }

  // Increment use count
  await supabase
    .from('team_invites')
    .update({ use_count: invite.use_count + 1 })
    .eq('id', invite.id);

  return {
    teamId: invite.team_id,
    teamName: (invite.teams as any)?.name || 'Unknown Team',
  };
};

export const getInviteLink = (code: string): string => {
  const baseUrl = window.location.origin;
  return `${baseUrl}/invite/${code}`;
};
```

**Step 2: Commit**

```bash
git add frontend/services/inviteService.ts
git commit -m "feat: add invite service with create, fetch, delete, accept"
```

### Task 5.2: Create InviteMembersModal component

**Files:**
- Create: `frontend/components/InviteMembersModal.tsx`

**Step 1: Create the modal**

```tsx
import React, { useState, useEffect } from 'react';
import { X, Copy, Check, Loader2, Link2, Clock, Users, Trash2, RefreshCw } from 'lucide-react';
import { TeamInvite, createInvite, fetchInvites, deleteInvite, getInviteLink } from '../services/inviteService';

interface InviteMembersModalProps {
  isOpen: boolean;
  onClose: () => void;
  teamId: string;
  teamName: string;
}

type ExpiryOption = '30m' | '1h' | '6h' | '12h' | '1d' | '7d' | 'never';
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
                    onChange={(e) => setMaxUses(e.target.value === 'null' ? null : parseInt(e.target.value))}
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
```

**Step 2: Commit**

```bash
git add frontend/components/InviteMembersModal.tsx
git commit -m "feat: add InviteMembersModal with link generation and settings"
```

---

## Phase 6: Integration

### Task 6.1: Integrate all components in SettingsModal

**Files:**
- Modify: `frontend/components/SettingsModal.tsx`

**Step 1: Update SettingsModal with all imports and state**

```tsx
import React, { useState, useEffect } from 'react';
import { X, User, Users, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { PersonalSettings } from './PersonalSettings';
import { TeamSettings } from './TeamSettings';
import { InviteMembersModal } from './InviteMembersModal';

// ... rest of the component with integrated sections
```

Update the content area to use actual components and add invite modal state.

**Step 2: Commit**

```bash
git add frontend/components/SettingsModal.tsx
git commit -m "feat: integrate PersonalSettings, TeamSettings, and InviteMembersModal"
```

### Task 6.2: Update UserDropdown to sort teams

**Files:**
- Modify: `frontend/components/UserDropdown.tsx`

**Step 1: Sort teams with active team first**

In UserDropdown, modify the teams rendering:

```tsx
// Sort teams: active team first, then rest alphabetically
const sortedTeams = [...teams].sort((a, b) => {
  if (a.id === activeTeamId) return -1;
  if (b.id === activeTeamId) return 1;
  return a.name.localeCompare(b.name);
});

// Then map over sortedTeams instead of teams
{sortedTeams.map(team => (
  // existing team row JSX
))}
```

**Step 2: Commit**

```bash
git add frontend/components/UserDropdown.tsx
git commit -m "feat: sort teams with selected team at top"
```

### Task 6.3: Wire up SettingsModal in App.tsx

**Files:**
- Modify: `frontend/App.tsx`

**Step 1: Add state and handlers for SettingsModal**

Add to App.tsx:
- Import SettingsModal
- Add `isSettingsModalOpen` state
- Add `settingsInitialTab` state
- Update `onTeamSettings` handler to open modal with team tab
- Update `onProfile`/`onAccount` to open modal with personal tab

**Step 2: Commit**

```bash
git add frontend/App.tsx
git commit -m "feat: wire up SettingsModal in main app"
```

---

## Phase 7: Database Migration Execution

### Task 7.1: Run migration via Supabase MCP

**Step 1: Execute migration**

Use Supabase MCP `apply_migration` or SQL Editor to run `022_create_team_invites_table.sql`.

**Step 2: Verify tables created**

```sql
SELECT * FROM team_invites LIMIT 1;
```

---

## Summary

This plan implements:
1. Database migration for team_invites table
2. Full-screen SettingsModal with sidebar navigation
3. PersonalSettings with profile editing and password change
4. TeamSettings with members table, role display, and danger zone
5. InviteMembersModal with link generation and settings
6. Team dropdown reordering (active team first)
7. Integration in App.tsx

**Estimated commits:** 10+
**Files created:** 5 new components, 1 new service, 1 migration
**Files modified:** teamService.ts, UserDropdown.tsx, App.tsx
