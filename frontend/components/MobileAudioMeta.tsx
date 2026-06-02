import React, { useState, useEffect, useCallback } from 'react';
import { Heart, MessageCircle, Share2, Bookmark } from 'lucide-react';
import type { Video, Tag } from '../types';
import type { SodaTheme } from '../utils/sodaTheme';
import { getSupabaseClient } from '../supabaseClient';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { EagleTagPicker } from './EagleTagPicker';
import { RatingStars } from './detail/DetailCardKit';

/**
 * MobileAudioMeta — Soda Music-style "folded-in" meta strip for the mobile
 * audio player page. NO card chrome (transparent background, no border): it sits
 * directly on the player page below the audio controls.
 *
 * Shows only the lightweight fields Soda surfaces: a centered social-stats row,
 * editable tags, rating, and notes. Deliberately omits ID / Release Time /
 * Duration / AI buttons / description / the Overview·Lyrics tabs.
 *
 * Mobile-audio ONLY — desktop and mobile-video keep rendering <VideoDetailPanel>.
 */

interface MobileAudioMetaProps {
  video: Video;
  resourceId?: string;
  resourceRating?: number;
  resourceNotes?: string;
  onRatingChange?: (n: number) => void;
  onNotesChange?: (s: string) => void;
  onNotesBlur?: () => void;
  theme?: SodaTheme;
}

function formatNumber(num?: number | null): string {
  if (!num || num <= 0) return '0';
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return String(num);
}

interface StatDef {
  key: string;
  icon: React.ReactNode;
  value?: number | null;
}

/** A single icon + count column in the social stats row. */
function SocialStat({ icon, value }: { icon: React.ReactNode; value?: number | null }) {
  return (
    <div className="flex flex-col items-center gap-1 min-w-[56px]">
      {icon}
      <span className="text-xs font-medium text-white/70">{formatNumber(value)}</span>
    </div>
  );
}

export function MobileAudioMeta({
  video,
  resourceId,
  resourceRating,
  resourceNotes,
  onRatingChange,
  onNotesChange,
  onNotesBlur,
  theme,
}: MobileAudioMetaProps) {
  // Resource tags — same fetch pattern as MediaCard, keyed off the resolved
  // resource id (prop preferred, else look up by media_id).
  const [resolvedResourceId, setResolvedResourceId] = useState<string | null>(resourceId ?? null);
  const [resourceTags, setResourceTags] = useState<Array<{ tag: Tag }>>([]);
  const [allTags, setAllTags] = useState<Tag[]>([]);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch((err) => console.error('Failed to load tags:', err));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setResourceTags([]);

    (async () => {
      // Resolve the resource id: prefer the prop, otherwise look it up via media_id.
      let resId = resourceId ?? null;
      if (!resId) {
        if (!video.id) return;
        const supabase = getSupabaseClient();
        if (!supabase) return;
        const { data: resource } = await supabase
          .from('resources')
          .select('id')
          .eq('media_id', video.id)
          .limit(1)
          .maybeSingle();
        if (cancelled || !resource) return;
        resId = String(resource.id);
      }
      if (cancelled) return;
      setResolvedResourceId(resId);
      try {
        const tags = await fetchResourceTags(resId);
        if (!cancelled) setResourceTags(tags);
      } catch (err) {
        console.error('Failed to load resource tags:', err);
      }
    })();

    return () => { cancelled = true; };
  }, [resourceId, video.id]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!resolvedResourceId) return;
    try {
      await addResourceTag(resolvedResourceId, tagId);
      const updated = await fetchResourceTags(resolvedResourceId);
      setResourceTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [resolvedResourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!resolvedResourceId) return;
    try {
      await removeResourceTag(resolvedResourceId, tagId);
      setResourceTags(prev => prev.filter(t => String(t.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [resolvedResourceId]);

  const handleCreateTag = useCallback(async (name: string, color: string): Promise<Tag | null> => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch (err) {
      console.error('Failed to create tag:', err);
      return null;
    }
  }, []);

  // Social stats — skip any that are 0/missing (Soda 图3 style).
  const accent = theme?.accent;
  const stats: StatDef[] = [
    { key: 'like', icon: <Heart size={18} style={accent ? { color: accent } : undefined} className={accent ? '' : 'text-white/70'} />, value: video.like_count },
    { key: 'comment', icon: <MessageCircle size={18} className="text-white/70" />, value: video.comment_count },
    { key: 'share', icon: <Share2 size={18} className="text-white/70" />, value: video.share_count },
    { key: 'collect', icon: <Bookmark size={18} className="text-white/70" />, value: video.favorite_count },
  ];
  const visibleStats = stats.filter(s => (s.value ?? 0) > 0);

  return (
    <div className="w-full flex flex-col items-center gap-6 px-5 py-6">
      {/* Social stats row */}
      {visibleStats.length > 0 && (
        <div className="flex items-center justify-center gap-8">
          {visibleStats.map(s => (
            <SocialStat key={s.key} icon={s.icon} value={s.value} />
          ))}
        </div>
      )}

      {/* Tags — editable picker (same wiring as MediaCard) */}
      {resolvedResourceId && (
        <div className="w-full">
          <EagleTagPicker
            assignedTags={resourceTags.map(item => item.tag).filter((t): t is Tag => !!t)}
            allTags={allTags}
            onAdd={handleAddTag}
            onRemove={handleRemoveTag}
            onCreate={handleCreateTag}
          />
        </div>
      )}

      {/* Rating */}
      {onRatingChange && (
        <div className="flex items-center gap-4">
          <span className="text-xs text-white/40 uppercase tracking-wider">Rating</span>
          <RatingStars value={resourceRating || 0} onChange={onRatingChange} />
        </div>
      )}

      {/* Notes */}
      {onNotesChange && (
        <div className="w-full">
          <textarea
            value={resourceNotes || ''}
            onChange={(e) => onNotesChange(e.target.value)}
            onBlur={onNotesBlur}
            placeholder="Add notes..."
            rows={2}
            className="w-full bg-zinc-800/50 border border-zinc-700/50 rounded-lg px-3 py-2 text-sm text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-indigo-500/50 resize-none"
          />
        </div>
      )}
    </div>
  );
}
