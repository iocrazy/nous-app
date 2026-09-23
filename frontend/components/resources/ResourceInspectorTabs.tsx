// frontend/components/resources/ResourceInspectorTabs.tsx
//
// ResourceDetailPage's inspector tab row, the same shape as VideoDetailPanel:
//   Overview | AI | Shots | Review | Lyrics
// Review stays a fourth big tab: its annotation state drives the player
// overlay, so it cannot fold into another tab.

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Brain, Clapperboard, Eye, Music, Pencil } from 'lucide-react';

export type InspectorTab = 'info' | 'ai' | 'shots' | 'review' | 'lyrics';

export interface InspectorKind {
  isVideo: boolean;
  isAudio: boolean;
  isUploadedAudio: boolean;
}

/** Tabs a resource of this kind shows, in display order. */
export function visibleInspectorTabs({ isVideo, isAudio, isUploadedAudio }: InspectorKind): InspectorTab[] {
  const tabs: InspectorTab[] = ['info'];
  if (!isUploadedAudio && (isVideo || isAudio)) tabs.push('ai');
  if (!isUploadedAudio && isVideo) tabs.push('shots');
  if (!isUploadedAudio) tabs.push('review');
  if (isUploadedAudio) tabs.push('lyrics');
  return tabs;
}

type Translate = (key: string, def: string) => string;

const TAB_META: Record<InspectorTab, { icon: React.ReactNode; label: (t: Translate) => string }> = {
  info: { icon: <Eye size={16} />, label: (t) => t('detail.tabs.overview', 'Overview') },
  ai: { icon: <Brain size={16} />, label: (t) => t('detail.tabs.ai', 'AI') },
  shots: { icon: <Clapperboard size={16} />, label: (t) => t('detail.tabs.shots', 'Shots') },
  review: { icon: <Pencil size={16} />, label: (t) => t('detail.tabs.review', 'Review') },
  lyrics: { icon: <Music size={16} />, label: (t) => t('detail.tabs.lyrics', 'Lyrics') },
};

export interface ResourceInspectorTabsProps {
  tabs: readonly InspectorTab[];
  active: InspectorTab;
  onChange: (tab: InspectorTab) => void;
  /** Classes for an inactive tab (the page's theme-aware text/hover ladder). */
  inactiveClass: string;
  /** Status dot shown on the AI tab. */
  aiIndicator?: React.ReactNode;
  className?: string;
  /** Rendered after the tabs (e.g. the island collapse button). */
  trailing?: React.ReactNode;
}

export const ResourceInspectorTabs: React.FC<ResourceInspectorTabsProps> = ({
  tabs,
  active,
  onChange,
  inactiveClass,
  aiIndicator,
  className = '',
  trailing,
}) => {
  const { t } = useTranslation();
  return (
    <div data-testid="inspector-tabs" className={`flex ${className}`}>
      {tabs.map((tab) => (
        <button
          key={tab}
          type="button"
          data-tab={tab}
          onClick={() => onChange(tab)}
          className={`flex items-center gap-2 px-4 py-3 text-sm font-medium transition-colors border-b-2 ${
            active === tab ? 'border-accent text-[var(--accent-text)]' : `border-transparent ${inactiveClass}`
          }`}
        >
          {TAB_META[tab].icon}
          {TAB_META[tab].label(t)}
          {tab === 'ai' ? aiIndicator : null}
        </button>
      ))}
      {trailing}
    </div>
  );
};

export default ResourceInspectorTabs;
