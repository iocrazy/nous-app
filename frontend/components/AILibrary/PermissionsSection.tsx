import { useTranslation } from 'react-i18next';
import type {
  AgentCapabilities,
  AgentChatPermissions,
  AgentMediaCaps,
  AgentWriteLevel,
} from '../../types';

interface Props {
  value: AgentChatPermissions;
  onChange: (next: AgentChatPermissions) => void;
  capabilities: AgentCapabilities;
  onCapabilitiesChange: (next: AgentCapabilities) => void;
}

/** Ceiling enforced by the backend schema (MAX_MEDIA_CALLS_PER_TURN). */
const MAX_MEDIA_CALLS = 100;

const WRITE_LEVELS: AgentWriteLevel[] = ['none', 'read', 'propose', 'write'];

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
        // The visible label is a sibling node, so without this the switch has
        // no accessible name — a screen reader announced only "switch, on".
        aria-label={label}
        aria-checked={checked}
        onClick={() => onToggle(!checked)}
        className={`relative h-6 w-10 flex-none rounded-full transition-colors ${
          checked ? 'bg-indigo-500' : 'bg-ink-700'
        }`}
      >
        {/* left-0 is load-bearing: an absolutely-positioned element with no
            left/right sits at its STATIC position, and <button> centers its
            content — so the knob started mid-track and the ON translate
            pushed it outside the pill entirely. */}
        <span
          className={`absolute left-0 top-0.5 h-5 w-5 rounded-full bg-white transition-transform ${
            checked ? 'translate-x-[18px]' : 'translate-x-0.5'
          }`}
        />
      </button>
    </div>
  );
}

/** Segmented 3-way (plus revoke) control for the write tier. A radiogroup
 *  rather than a toggle: the tiers are ordered and mutually exclusive, and
 *  'none' has to be reachable so a grant can actually be taken back. */
function WriteLevelPicker({
  value,
  onSelect,
}: {
  value: AgentWriteLevel;
  onSelect: (v: AgentWriteLevel) => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="py-3 border-b border-line">
      <div className="text-sm font-medium text-content">
        {t('aiLibrary.permissions.writeLevel')}
      </div>
      <div className="text-xs text-content-3 mt-0.5 mb-2">
        {t('aiLibrary.permissions.writeLevelDesc')}
      </div>
      <div role="radiogroup" aria-label={t('aiLibrary.permissions.writeLevel')} className="flex gap-1">
        {WRITE_LEVELS.map((lvl) => {
          const active = value === lvl;
          return (
            <button
              key={lvl}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onSelect(lvl)}
              className={`flex-1 rounded-md border px-2 py-1.5 text-xs font-medium transition-colors ${
                active
                  ? 'border-agent-line bg-agent-soft text-agent'
                  : 'border-line text-content-3 hover:text-content'
              }`}
            >
              {t(`aiLibrary.permissions.writeLevel_${lvl}`)}
            </button>
          );
        })}
      </div>
      <div className="text-xs text-content-3 mt-1.5">
        {t(`aiLibrary.permissions.writeLevelHint_${value}`)}
      </div>
    </div>
  );
}

export default function PermissionsSection({
  value,
  onChange,
  capabilities,
  onCapabilitiesChange,
}: Props) {
  const { t } = useTranslation();
  const enabled = value.enabled ?? false;
  const set = (patch: Partial<AgentChatPermissions>) => onChange({ ...value, ...patch });

  const caps = capabilities;
  const media: AgentMediaCaps = caps.media ?? {};
  const setCaps = (patch: Partial<AgentCapabilities>) =>
    onCapabilitiesChange({ ...caps, ...patch });
  // Merge into the existing media object — the backend deep-merges too, but
  // sending a whole `media` that dropped a sibling key would still be a lie
  // about what the user changed.
  const setMedia = (patch: Partial<AgentMediaCaps>) =>
    setCaps({ media: { ...media, ...patch } });

  const mediaOn = (media.image ?? false) || (media.video ?? false);

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

      {/* High-risk grants (spec §2). Separate heading because these are a
          different kind of decision from chat participation: they let the
          agent change or destroy the user's work, or spend money. */}
      <h3 className="mt-8 text-sm font-semibold text-content">
        {t('aiLibrary.permissions.capabilitiesTitle')}
      </h3>
      <p className="text-xs text-content-3 mt-1 mb-2">
        {t('aiLibrary.permissions.capabilitiesIntro')}
      </p>

      <WriteLevelPicker
        value={caps.write_level ?? 'none'}
        onSelect={(v) => setCaps({ write_level: v })}
      />

      <Toggle
        label={t('aiLibrary.permissions.deleteCap')}
        desc={t('aiLibrary.permissions.deleteCapDesc')}
        checked={caps.delete ?? false}
        onToggle={(v) => setCaps({ delete: v })}
      />

      <Toggle
        label={t('aiLibrary.permissions.generateImage')}
        desc={t('aiLibrary.permissions.generateImageDesc')}
        checked={media.image ?? false}
        onToggle={(v) => setMedia({ image: v })}
      />
      <Toggle
        label={t('aiLibrary.permissions.generateVideo')}
        desc={t('aiLibrary.permissions.generateVideoDesc')}
        checked={media.video ?? false}
        onToggle={(v) => setMedia({ video: v })}
      />

      {/* The cap only means anything once a media kind is granted, but it stays
          editable rather than hidden so a pre-set limit is visible before the
          toggle is flipped. */}
      <div className={`py-3 border-b border-line ${mediaOn ? '' : 'opacity-40'}`}>
        <label
          htmlFor="media-cap"
          className="block text-sm font-medium text-content"
        >
          {t('aiLibrary.permissions.mediaCap')}
        </label>
        <div className="text-xs text-content-3 mt-0.5 mb-2">
          {t('aiLibrary.permissions.mediaCapDesc')}
        </div>
        <input
          id="media-cap"
          type="number"
          min={0}
          max={MAX_MEDIA_CALLS}
          value={media.max_calls_per_turn ?? 4}
          onChange={(e) => {
            // Clamp here so the PATCH can't 422 on a value the field allowed
            // the user to type. Empty input falls back to the backend default
            // rather than sending NaN.
            const raw = Number.parseInt(e.target.value, 10);
            const next = Number.isNaN(raw)
              ? 4
              : Math.min(MAX_MEDIA_CALLS, Math.max(0, raw));
            setMedia({ max_calls_per_turn: next });
          }}
          className="w-24 rounded-md border border-line bg-transparent px-2 py-1 text-sm text-content"
        />
      </div>

      <Toggle
        label={t('aiLibrary.permissions.crossEpisodeRead')}
        desc={t('aiLibrary.permissions.crossEpisodeReadDesc')}
        checked={caps.cross_episode_read ?? false}
        onToggle={(v) => setCaps({ cross_episode_read: v })}
      />
      <Toggle
        label={t('aiLibrary.permissions.externalPublish')}
        desc={t('aiLibrary.permissions.externalPublishDesc')}
        checked={caps.external_publish ?? false}
        onToggle={(v) => setCaps({ external_publish: v })}
      />
    </div>
  );
}
