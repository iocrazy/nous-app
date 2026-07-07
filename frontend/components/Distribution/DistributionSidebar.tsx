import React from 'react';
import { NavLink } from 'react-router-dom';
import { Users, Send, LineChart, Settings } from 'lucide-react';

const Item: React.FC<{ to?: string; icon: React.ElementType; label: string; disabled?: boolean }> =
  ({ to, icon: Icon, label, disabled }) =>
    disabled ? (
      <div className="flex items-center gap-3 rounded-md px-3 py-2 text-[13px] text-ink-600 cursor-not-allowed">
        <Icon size={15} /><span className="flex-1">{label}</span>
        <span className="text-[10px] text-ink-600">Soon</span>
      </div>
    ) : (
      <NavLink
        to={to!}
        className={({ isActive }) =>
          `flex items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors ${
            isActive ? 'bg-indigo-500/10 text-indigo-300' : 'text-ink-400 hover:bg-ink-800/50 hover:text-ink-200'
          }`
        }
      >
        <Icon size={15} /><span className="flex-1">{label}</span>
      </NavLink>
    );

export const DistributionSidebar: React.FC = () => (
  <div className="flex w-52 flex-col overflow-y-auto border-r border-ink-800/40">
    <div className="px-4 pt-4 pb-3">
      <span className="text-sm font-semibold text-ink-200">Distribution</span>
    </div>
    <div className="px-2 flex flex-col gap-0.5">
      <Item icon={Send} label="Publish" disabled />
      <div className="mx-3 my-2 border-t border-ink-800/80" />
      <span className="px-2 text-[11px] font-medium uppercase tracking-wider text-ink-600">Accounts</span>
      <Item to="accounts" icon={Users} label="All accounts" />
      <div className="mx-3 my-2 border-t border-ink-800/80" />
      <span className="px-2 text-[11px] font-medium uppercase tracking-wider text-ink-600">Insights</span>
      <Item icon={LineChart} label="Analytics" disabled />
      <Item icon={Settings} label="Settings" disabled />
    </div>
  </div>
);

export default DistributionSidebar;
