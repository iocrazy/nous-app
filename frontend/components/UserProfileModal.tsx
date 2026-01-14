import React, { useState } from 'react';
import { UserProfile } from '../types';
import { X, User, Mail, Link as LinkIcon, Save, Camera, Lock, LogOut } from 'lucide-react';

interface UserProfileModalProps {
  user: UserProfile;
  isOpen: boolean;
  onClose: () => void;
  onSave: (user: UserProfile) => void;
  onLogout?: () => void;
}

export const UserProfileModal: React.FC<UserProfileModalProps> = ({ user, isOpen, onClose, onSave, onLogout }) => {
  const [formData, setFormData] = useState<UserProfile>(user);
  const [passwords, setPasswords] = useState({ new: '', confirm: '' });

  if (!isOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (passwords.new && passwords.new !== passwords.confirm) {
      alert("Passwords do not match!");
      return;
    }
    if (passwords.new) {
        alert("Password updated successfully!");
    }
    onSave(formData);
    onClose();
  };

  const handleLogoutClick = () => {
    if (onLogout) {
      onLogout();
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-zinc-900 border border-zinc-800 rounded-2xl w-full max-w-md shadow-2xl relative overflow-hidden flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-800 flex justify-between items-center bg-zinc-900/50">
          <h2 className="text-lg font-semibold text-zinc-100">Edit Profile</h2>
          <button onClick={onClose} className="text-zinc-500 hover:text-zinc-300 transition-colors">
            <X size={20} />
          </button>
        </div>

        <div className="overflow-y-auto">
            <form onSubmit={handleSubmit} className="p-6 space-y-8">
            {/* Avatar Section */}
            <div className="flex flex-col items-center gap-4">
                <div className="relative group">
                    <div className="w-24 h-24 rounded-full overflow-hidden border-2 border-indigo-500/30 bg-zinc-800">
                    {formData.avatarUrl ? (
                        <img src={formData.avatarUrl} alt="Avatar" className="w-full h-full object-cover" />
                    ) : (
                        <div className="w-full h-full flex items-center justify-center text-zinc-500"><User size={32}/></div>
                    )}
                    </div>
                    <div className="absolute inset-0 bg-black/40 rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer">
                    <Camera className="text-white w-6 h-6" />
                    </div>
                </div>
                <div className="text-center">
                    <h3 className="text-white font-medium">{formData.name}</h3>
                    <span className="px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 text-xs border border-indigo-500/20">{formData.plan}</span>
                </div>
            </div>

            {/* General Info */}
            <div className="space-y-4">
                <h4 className="text-sm font-semibold text-zinc-200 border-b border-zinc-800 pb-2">Basic Information</h4>
                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-zinc-400 ml-1">Display Name</label>
                    <div className="flex items-center bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 focus-within:border-indigo-500 transition-colors">
                    <User className="w-4 h-4 text-zinc-500 mr-3" />
                    <input 
                        type="text" 
                        value={formData.name}
                        onChange={e => setFormData({...formData, name: e.target.value})}
                        className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
                        placeholder="Enter your name"
                    />
                    </div>
                </div>

                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-zinc-400 ml-1">Email Address</label>
                    <div className="flex items-center bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 focus-within:border-indigo-500 transition-colors">
                    <Mail className="w-4 h-4 text-zinc-500 mr-3" />
                    <input 
                        type="email" 
                        value={formData.email}
                        onChange={e => setFormData({...formData, email: e.target.value})}
                        className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
                        placeholder="name@example.com"
                    />
                    </div>
                </div>

                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-zinc-400 ml-1">Avatar URL</label>
                    <div className="flex items-center bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 focus-within:border-indigo-500 transition-colors">
                    <LinkIcon className="w-4 h-4 text-zinc-500 mr-3" />
                    <input 
                        type="text" 
                        value={formData.avatarUrl}
                        onChange={e => setFormData({...formData, avatarUrl: e.target.value})}
                        className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
                        placeholder="https://..."
                    />
                    </div>
                </div>
            </div>

            {/* Security Section */}
            <div className="space-y-4">
                <h4 className="text-sm font-semibold text-zinc-200 border-b border-zinc-800 pb-2">Security</h4>
                <div className="space-y-1.5">
                    <label className="text-xs font-medium text-zinc-400 ml-1">New Password</label>
                    <div className="flex items-center bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 focus-within:border-indigo-500 transition-colors">
                    <Lock className="w-4 h-4 text-zinc-500 mr-3" />
                    <input 
                        type="password" 
                        value={passwords.new}
                        onChange={e => setPasswords({...passwords, new: e.target.value})}
                        className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
                        placeholder="Leave blank to keep current"
                    />
                    </div>
                </div>
                {passwords.new && (
                    <div className="space-y-1.5 animate-in slide-in-from-top-2 duration-200">
                        <label className="text-xs font-medium text-zinc-400 ml-1">Confirm New Password</label>
                        <div className="flex items-center bg-zinc-950 border border-zinc-800 rounded-lg px-3 py-2.5 focus-within:border-indigo-500 transition-colors">
                        <Lock className="w-4 h-4 text-zinc-500 mr-3" />
                        <input 
                            type="password" 
                            value={passwords.confirm}
                            onChange={e => setPasswords({...passwords, confirm: e.target.value})}
                            className="bg-transparent border-none outline-none text-sm text-zinc-200 w-full placeholder-zinc-600"
                            placeholder="Re-enter new password"
                        />
                        </div>
                    </div>
                )}
            </div>

            <div className="pt-2 space-y-3">
                <button 
                    type="submit"
                    className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
                >
                    <Save size={18} />
                    Save Changes
                </button>
                
                {onLogout && (
                  <button 
                      type="button"
                      onClick={handleLogoutClick}
                      className="w-full bg-zinc-950 hover:bg-zinc-800 text-red-400 border border-zinc-800 hover:border-red-900/30 font-medium py-2.5 rounded-lg transition-colors flex items-center justify-center gap-2"
                  >
                      <LogOut size={18} />
                      Sign Out
                  </button>
                )}
            </div>
            </form>
        </div>
      </div>
    </div>
  );
};
