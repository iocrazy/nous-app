import React, { useState } from 'react';
import { UserProfile } from '../types';
import { X, User, Camera } from 'lucide-react';

interface UserProfileModalProps {
  user: UserProfile;
  isOpen: boolean;
  onClose: () => void;
  onSave: (user: UserProfile) => void;
  onLogout?: () => void;
}

export const UserProfileModal: React.FC<UserProfileModalProps> = ({ user, isOpen, onClose, onSave, onLogout }) => {
  const [formData, setFormData] = useState<UserProfile>(user);

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave(formData);
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-end sm:items-center justify-center sm:p-4 animate-in fade-in duration-200">
      <div className="bg-ink-900 sm:border sm:border-ink-800 sm:rounded-2xl w-full sm:max-w-md shadow-2xl relative overflow-hidden flex flex-col max-h-[95vh] sm:max-h-[90vh] rounded-t-2xl sm:rounded-2xl">
        {/* Header */}
        <div className="px-5 pt-[max(env(safe-area-inset-top),16px)] pb-4 border-b border-ink-800 flex justify-between items-center bg-ink-900/50">
          <h2 className="text-lg font-semibold text-ink-100">Edit Profile</h2>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-300 transition-colors p-1">
            <X size={20} />
          </button>
        </div>

        <div className="overflow-y-auto">
            <form onSubmit={handleSubmit} className="p-5 sm:p-6 space-y-6 sm:space-y-8">
            {/* Avatar Section */}
            <div className="flex flex-col items-center gap-3">
                <div className="relative group">
                    <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-full overflow-hidden border-2 border-indigo-500/30 bg-ink-800">
                    {formData.avatarUrl ? (
                        <img src={formData.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center text-ink-500"><User size={28}/></div>
                    )}
                    </div>
                    <div className="absolute inset-0 bg-black/40 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer">
                    <Camera className="text-white w-5 h-5" />
                    </div>
                </div>
                <div className="text-center">
                    <p className="text-sm text-ink-400">Profile Photo</p>
                    <p className="text-xs text-ink-600 mt-0.5">Click the camera icon to upload a new photo</p>
                </div>
            </div>

            {/* Avatar URL input */}
            <div>
                <div className="flex items-center bg-ink-950 border border-ink-800 rounded-xl px-3.5 py-3 focus-within:border-indigo-500 transition-colors">
                <input
                    type="text"
                    value={formData.avatarUrl}
                    onChange={e => setFormData({...formData, avatarUrl: e.target.value})}
                    className="bg-transparent border-none outline-none text-sm text-ink-200 w-full placeholder-ink-600"
                    placeholder="Or paste image URL"
                />
                </div>
            </div>

            {/* Form fields */}
            <div className="space-y-4">
                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-ink-400 ml-1">Username</label>
                    <div className="flex items-center bg-ink-950 border border-ink-800 rounded-xl px-3.5 py-3 focus-within:border-indigo-500 transition-colors">
                    <input
                        type="text"
                        value={formData.name}
                        onChange={e => setFormData({...formData, name: e.target.value})}
                        className="bg-transparent border-none outline-none text-[15px] text-ink-200 w-full placeholder-ink-600"
                        placeholder="Enter your name"
                    />
                    </div>
                </div>

                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-ink-400 ml-1">Bio</label>
                    <div className="bg-ink-950 border border-ink-800 rounded-xl px-3.5 py-3 focus-within:border-indigo-500 transition-colors">
                    <textarea
                        value={formData.bio || ''}
                        onChange={e => setFormData({...formData, bio: e.target.value})}
                        rows={3}
                        className="bg-transparent border-none outline-none text-[15px] text-ink-200 w-full placeholder-ink-600 resize-none"
                        placeholder="Tell us about yourself..."
                    />
                    </div>
                </div>

                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-ink-400 ml-1">Email</label>
                    <div className="flex items-center bg-ink-950 border border-ink-800 rounded-xl px-3.5 py-3">
                    <input
                        type="email"
                        value={formData.email}
                        disabled
                        className="bg-transparent border-none outline-none text-[15px] text-ink-500 w-full"
                    />
                    </div>
                    <p className="text-xs text-ink-600 ml-1">Email cannot be changed</p>
                </div>

                <button
                    type="submit"
                    className="w-full bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white font-medium py-3 rounded-xl transition-colors text-[15px]"
                >
                    Save Changes
                </button>
            </div>

            {/* Security Section */}
            <div className="space-y-3">
                <h4 className="text-[15px] font-bold text-ink-100">Security</h4>
                <button
                    type="button"
                    className="w-full text-left bg-ink-950 border border-ink-800 rounded-xl px-4 py-3 text-[15px] text-ink-300 hover:bg-ink-800/60 active:bg-ink-800 transition-colors"
                >
                    Change Password
                </button>
            </div>

            {/* Bottom spacer for mobile */}
            <div className="pb-[env(safe-area-inset-bottom,16px)]" />
            </form>
        </div>
      </div>
    </div>
  );
};
