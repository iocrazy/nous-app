import React, { useState } from 'react';
import { Camera, Loader2, Check, AlertCircle, Eye, EyeOff } from 'lucide-react';
import { getAuthHeaders } from '../services/parserService';

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

const getApiUrl = (): string => {
  // @ts-ignore
  if (typeof import.meta !== 'undefined' && 'VITE_API_URL' in import.meta.env) {
    // @ts-ignore
    return import.meta.env.VITE_API_URL || '';
  }
  return 'http://localhost:8080';
};

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

  const handleSaveProfile = async () => {
    setIsSaving(true);
    setError(null);
    setSaveSuccess(false);

    try {
      const apiUrl = getApiUrl();
      const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          username: name,
          // Note: bio and avatar_url are stored in user metadata
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: 'Update failed' }));
        throw new Error(data.detail || 'Failed to update profile');
      }

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
      const apiUrl = getApiUrl();
      const response = await fetch(`${apiUrl}/api/v1/auth/me`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify({
          password: newPassword,
        }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({ detail: 'Update failed' }));
        throw new Error(data.detail || 'Failed to change password');
      }

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

        {/* Password Success Message */}
        {passwordSuccess && (
          <div className="mb-4 p-3 bg-green-500/10 border border-green-500/20 rounded-lg text-green-400 text-sm flex items-center gap-2">
            <Check size={14} />
            Password changed successfully
          </div>
        )}

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
