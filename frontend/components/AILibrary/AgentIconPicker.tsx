import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AGENT_ICON_OPTIONS, getAgentIcon } from './agentIcons';

interface AgentIconPickerProps {
  /** Currently selected icon slug, or null for default. */
  value?: string | null;
  /** Called with the newly chosen slug when the user picks. */
  onChange: (slug: string) => void;
  /** Size of the rendered trigger icon. */
  size?: number;
  /** Disable the trigger (read-only, e.g. system preset agents). */
  disabled?: boolean;
}

/**
 * Click-to-open popover with a searchable grid of lucide icons an agent can
 * adopt. Mirrors paperclip's AgentIconPicker pattern: input at top, grid
 * below, click an icon to commit the choice and close.
 */
export const AgentIconPicker: React.FC<AgentIconPickerProps> = ({
  value,
  onChange,
  size = 28,
  disabled = false,
}) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return AGENT_ICON_OPTIONS;
    return AGENT_ICON_OPTIONS.filter((o) => o.slug.toLowerCase().includes(q));
  }, [search]);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as Node;
      if (
        popoverRef.current?.contains(target) ||
        triggerRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEsc);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEsc);
    };
  }, [open]);

  const CurrentIcon = getAgentIcon(value);

  return (
    <div className="relative inline-block">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => !disabled && setOpen((v) => !v)}
        className={`inline-flex items-center justify-center rounded-lg border border-zinc-700 bg-zinc-800 p-2 transition-colors ${
          disabled
            ? 'cursor-not-allowed opacity-60'
            : 'hover:border-indigo-500/50 hover:bg-zinc-700'
        }`}
        title={disabled ? undefined : t('aiLibrary.agents.pickIcon', 'Pick icon')}
        aria-label={t('aiLibrary.agents.pickIcon', 'Pick icon')}
      >
        <CurrentIcon size={size} className="text-indigo-400" />
      </button>

      {open && (
        <div
          ref={popoverRef}
          className="absolute left-0 top-full z-50 mt-2 w-72 rounded-lg border border-zinc-700 bg-zinc-900 p-3 shadow-2xl"
        >
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('aiLibrary.agents.searchIcons', 'Search icons...')}
            className="mb-2 w-full rounded-md border border-zinc-700 bg-zinc-800 px-2 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-500 focus:border-indigo-500 focus:outline-none"
            autoFocus
          />
          <div className="grid max-h-56 grid-cols-7 gap-1 overflow-y-auto">
            {filtered.map(({ slug, Icon }) => {
              const active = value === slug;
              return (
                <button
                  key={slug}
                  type="button"
                  onClick={() => {
                    onChange(slug);
                    setOpen(false);
                    setSearch('');
                  }}
                  title={slug}
                  className={`flex h-8 w-8 items-center justify-center rounded transition-colors ${
                    active
                      ? 'bg-indigo-500/20 text-indigo-300'
                      : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'
                  }`}
                >
                  <Icon size={16} />
                </button>
              );
            })}
            {filtered.length === 0 && (
              <div className="col-span-7 px-2 py-4 text-center text-xs text-zinc-500">
                {t('common.noResults', 'No results')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

export default AgentIconPicker;
