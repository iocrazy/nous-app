import React, { useCallback, useEffect, useState } from 'react';
import { Camera, Loader2, Check, AlertCircle, Eye, EyeOff } from 'lucide-react';
import { getSupabaseClient } from '../supabaseClient';
import { fetchProfile, updateUsername, ProfileError, type Profile } from '../services/profileService';

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
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showNewPassword, setShowNewPassword] = useState(false);
  const [isChangingPassword, setIsChangingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSuccess, setPasswordSuccess] = useState(false);

  // 主账号名 —— the real one, from `user_profiles.username`. Kept separate from
  // the display_name/bio/avatar block below on purpose: those are free-form
  // metadata, this one is globally unique and answers 409 when taken, so it
  // needs its own save + its own error line.
  const [profile, setProfile] = useState<Profile | null>(null);
  const [accountName, setAccountName] = useState('');
  const [isSavingName, setIsSavingName] = useState(false);
  const [nameSaved, setNameSaved] = useState(false);
  const [nameError, setNameError] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    try {
      const p = await fetchProfile();
      setProfile(p);
      setAccountName(p.username);
    } catch (err) {
      // Not fatal — the rest of the panel still works. Say so rather than
      // rendering an empty box that looks like "you have no name".
      console.error('[PersonalSettings] failed to load profile', err);
      setNameError(err instanceof Error ? err.message : 'Failed to load account name');
    }
  }, []);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  const handleSaveAccountName = async () => {
    const wanted = accountName.trim();
    if (!wanted || wanted === profile?.username) return;

    setIsSavingName(true);
    setNameError(null);
    setNameSaved(false);
    try {
      const updated = await updateUsername(wanted);
      setProfile(updated);
      setAccountName(updated.username);
      setNameSaved(true);
      onUserUpdated?.();
      setTimeout(() => setNameSaved(false), 3000);
    } catch (err) {
      // The backend types its refusals; surface the one the user can act on
      // instead of a generic failure.
      if (err instanceof ProfileError && err.code === 'username_taken') {
        setNameError('That name is already taken — pick another one.');
      } else {
        setNameError(err instanceof Error ? err.message : 'Failed to save');
      }
    } finally {
      setIsSavingName(false);
    }
  };

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
    <div className="space-y-6 animate-in fade-in duration-300">
      {/* Account Section — 主账号名 + the stable numeric id.
          Two fields on purpose: the name is yours to change, the id is what
          identifies you and never moves. */}
      <div className="bg-ink-900/50 border border-ink-800/60 rounded-xl p-6">
        <h3 className="text-sm font-semibold text-ink-400 uppercase tracking-wider mb-5">
          Account
        </h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <div className="space-y-1.5">
            <label className="text-sm text-ink-400" htmlFor="account-name">
              Account Name
            </label>
            <div className="flex gap-2">
              <input
                id="account-name"
                type="text"
                value={accountName}
                onChange={(e) => {
                  setAccountName(e.target.value);
                  setNameError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleSaveAccountName();
                }}
                className="flex-1 min-w-0 bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500/50 transition-colors"
              />
              <button
                onClick={handleSaveAccountName}
                disabled={
                  isSavingName || !accountName.trim() || accountName.trim() === profile?.username
                }
                className="shrink-0 px-3 py-2 bg-ink-800 hover:bg-ink-700 disabled:opacity-40 disabled:hover:bg-ink-800 text-ink-200 text-sm rounded-lg transition-colors flex items-center gap-1.5"
              >
                {isSavingName ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : nameSaved ? (
                  <Check size={14} />
                ) : null}
                {nameSaved ? 'Saved' : 'Save'}
              </button>
            </div>
            {nameError ? (
              <p className="text-xs text-red-400 flex items-center gap-1">
                <AlertCircle size={12} />
                {nameError}
              </p>
            ) : (
              <p className="text-xs text-ink-600">Unique across everyone. You can change it.</p>
            )}
          </div>
          <div className="space-y-1.5">
            <label className="text-sm text-ink-400">ID</label>
            <input
              type="text"
              value={profile?.display_id ?? ''}
              disabled
              className="w-full bg-ink-950/30 border border-ink-800/50 rounded-lg px-3 py-2.5 text-sm text-ink-500 cursor-not-allowed font-mono"
            />
            <p className="text-xs text-ink-600">Never changes, even if you rename.</p>
          </div>
        </div>
      </div>

      {/* Profile Section */}
      <div className="bg-ink-900/50 border border-ink-800/60 rounded-xl p-6">
        <h3 className="text-sm font-semibold text-ink-400 uppercase tracking-wider mb-5">Profile</h3>

        {/* Avatar Row */}
        <div className="flex items-center gap-5 mb-6">
          <div className="relative flex-shrink-0">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center overflow-hidden ring-2 ring-ink-800">
              {avatarUrl ? (
                <img src={avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
              ) : (
                <span className="text-xl font-bold text-white">
                  {name.charAt(0).toUpperCase() || 'U'}
                </span>
              )}
            </div>
            <button className="absolute -bottom-0.5 -right-0.5 p-1.5 bg-ink-800 border border-ink-700 rounded-full hover:bg-ink-700 transition-colors">
              <Camera size={12} className="text-ink-300" />
            </button>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm text-ink-400 mb-1.5">Avatar URL</p>
            <input
              type="text"
              placeholder="Paste image URL..."
              value={avatarUrl}
              onChange={(e) => setAvatarUrl(e.target.value)}
              className="w-full bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2 text-sm text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 transition-colors"
            />
          </div>
        </div>

        {/* Form Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <div className="space-y-1.5">
            {/* Display name — free-form, Supabase Auth metadata. NOT the account
                name: this one is not unique and nothing identifies you by it.
                It used to be labelled "Username", which is why a rename here
                never changed the name anyone else saw. */}
            <label className="text-sm text-ink-400">Display Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500/50 transition-colors"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-sm text-ink-400">Email</label>
            <input
              type="email"
              value={user.email}
              disabled
              className="w-full bg-ink-950/30 border border-ink-800/50 rounded-lg px-3 py-2.5 text-sm text-ink-500 cursor-not-allowed"
            />
            <p className="text-xs text-ink-600">Cannot be changed</p>
          </div>
        </div>

        {/* Bio */}
        <div className="space-y-1.5 mt-5">
          <label className="text-sm text-ink-400">Bio</label>
          <textarea
            value={bio}
            onChange={(e) => setBio(e.target.value)}
            rows={2}
            placeholder="Tell us about yourself..."
            className="w-full bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2.5 text-sm text-ink-200 placeholder-ink-600 focus:outline-none focus:border-indigo-500/50 transition-colors resize-none"
          />
        </div>

        {/* Save */}
        <div className="flex items-center gap-3 mt-5 pt-4 border-t border-ink-800/50">
          <button
            onClick={handleSaveProfile}
            disabled={isSaving}
            className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white text-sm rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            {isSaving ? (
              <Loader2 size={14} className="animate-spin" />
            ) : saveSuccess ? (
              <Check size={14} />
            ) : null}
            {saveSuccess ? 'Saved!' : 'Save Changes'}
          </button>
          {error && (
            <span className="text-xs text-red-400 flex items-center gap-1">
              <AlertCircle size={12} />
              {error}
            </span>
          )}
        </div>
      </div>

      {/* Security Section */}
      <div className="bg-ink-900/50 border border-ink-800/60 rounded-xl p-6">
        <h3 className="text-sm font-semibold text-ink-400 uppercase tracking-wider mb-4">Security</h3>

        {passwordSuccess && (
          <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-sm flex items-center gap-2">
            <Check size={14} />
            Password changed successfully
          </div>
        )}

        {!showPasswordForm ? (
          <button
            onClick={() => setShowPasswordForm(true)}
            className="px-4 py-2 bg-ink-800 hover:bg-ink-700 text-sm text-ink-200 rounded-lg font-medium transition-colors"
          >
            Change Password
          </button>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-sm text-ink-400">New Password</label>
                <div className="relative">
                  <input
                    type={showNewPassword ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2.5 pr-10 text-sm text-ink-200 focus:outline-none focus:border-indigo-500/50"
                  />
                  <button
                    type="button"
                    onClick={() => setShowNewPassword(!showNewPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-ink-500 hover:text-ink-300"
                  >
                    {showNewPassword ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-sm text-ink-400">Confirm Password</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full bg-ink-950/50 border border-ink-800 rounded-lg px-3 py-2.5 text-sm text-ink-200 focus:outline-none focus:border-indigo-500/50"
                />
              </div>
            </div>

            {passwordError && (
              <p className="text-xs text-red-400 flex items-center gap-1">
                <AlertCircle size={12} />
                {passwordError}
              </p>
            )}

            <div className="flex gap-3">
              <button
                onClick={handleChangePassword}
                disabled={isChangingPassword}
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-600/50 text-white text-sm rounded-lg font-medium transition-colors flex items-center gap-2"
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
                className="px-4 py-2 text-sm text-ink-400 hover:text-ink-50 transition-colors"
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
