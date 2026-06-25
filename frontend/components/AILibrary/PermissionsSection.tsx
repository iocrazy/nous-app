import { useTranslation } from 'react-i18next';
import type { AgentChatPermissions } from '../../types';

interface Props {
  value: AgentChatPermissions;
  onChange: (next: AgentChatPermissions) => void;
}

function Toggle({
  label,
  desc,
  checked,
  onToggle,
}: {
  label: string;
  desc: string;
  checked: boolean;
  onToggle: (v: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b border-line">
      <div className="min-w-0">
        <div className="text-sm font-medium text-content">{label}</div>
        <div className="text-xs text-content-3 mt-0.5">{desc}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onToggle(!checked)}
        className={`relative h-6 w-10 flex-none rounded-full transition-colors ${
          checked ? 'bg-indigo-500' : 'bg-ink-700'
        }`}
      >
        <span
          className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}

export default function PermissionsSection({ value, onChange }: Props) {
  const { t } = useTranslation();
  const enabled = value.enabled ?? false;
  const set = (patch: Partial<AgentChatPermissions>) => onChange({ ...value, ...patch });

  return (
    <div className="max-w-xl">
      <p className="text-xs text-content-3 mb-4">
        {t('aiLibrary.permissions.intro')}
      </p>
      <Toggle
        label={t('aiLibrary.permissions.enableChat')}
        desc={t('aiLibrary.permissions.enableChatDesc')}
        checked={enabled}
        onToggle={(v) => set({ enabled: v })}
      />
      <div className={enabled ? '' : 'opacity-40 pointer-events-none'}>
        <Toggle
          label={t('aiLibrary.permissions.readTeamFiles')}
          desc={t('aiLibrary.permissions.readTeamFilesDesc')}
          checked={value.read_team_resources ?? false}
          onToggle={(v) => set({ read_team_resources: v })}
        />
        <Toggle
          label={t('aiLibrary.permissions.autoBroadcast')}
          desc={t('aiLibrary.permissions.autoBroadcastDesc')}
          checked={value.auto_broadcast ?? false}
          onToggle={(v) => set({ auto_broadcast: v })}
        />
      </div>
    </div>
  );
}
