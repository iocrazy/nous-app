import React, { useState, useEffect, useCallback } from 'react';
import { Music, MessageCircle, Share2, Bookmark, Star } from 'lucide-react';
import type { Video, Tag } from '../types';
import { RatingStars } from './detail/DetailCardKit';
import { EagleTagPicker } from './EagleTagPicker';
import { fetchResourceTags, addResourceTag, removeResourceTag } from '../services/resourceService';
import { fetchAllTags, createTag } from '../services/unifiedTagService';
import { getSupabaseClient } from '../supabaseClient';

interface AudioOverviewSideProps {
  /** The parsed_media row (download audio) — drives the social stats row and
   *  resolves the owning resource for tags. Omit for uploaded audio, which has
   *  no parsed_media / social stats; pass `resourceId` directly instead. */
  video?: Video;
  /** Uploaded-audio path: the resource id to bind tags to directly (skips the
   *  media_id → resource lookup). When set, the social stats row is hidden. */
  resourceId?: string;
  /** Album/track cover URL; falls back to a Music glyph on missing/404. */
  coverUrl?: string;
  title: string;
  author?: string;
  /** Resource-level rating (0–5) + setter; stars are read-only when no setter. */
  rating?: number;
  onRatingChange?: (rating: number) => void;
  /** When provided (uploaded audio), the cover becomes click-to-replace — mirrors
   *  the AudioHero cover affordance so island audio keeps cover upload (D12). */
  onCoverClick?: () => void;
}

// Same compact number format MediaCard uses (1.2K / 338.0K / 1.2M) so the stat
// row reads identically to the classic Overview card.
const formatNumber = (num?: number): string => {
  if (!num) return '0';
  if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
  return num.toString();
};

/**
 * Cover-side Overview column for the island audio stage — a faithful port of the
 * audio redesign mock `.side` block (mockups/mediahub-audio-redesign.html):
 * cover → song → artist → stats row → rating stars → tag row, centered and
 * compact under the cover. Replaces the `<MediaCard bare />` the stage used to
 * host so the cover-side matches the mock exactly. Stats/rating/tags are wired to
 * the SAME data + services MediaCard used (formatNumber, RatingStars,
 * EagleTagPicker), so add/remove/create tags and rating all keep working (D12).
 */
export const AudioOverviewSide: React.FC<AudioOverviewSideProps> = ({
  video,
  resourceId: explicitResourceId,
  coverUrl,
  title,
  author,
  rating,
  onRatingChange,
  onCoverClick,
}) => {
  // Resource tags — resolved self-contained from media_id exactly like MediaCard
  // (download path), or bound directly to an explicit resourceId (upload path),
  // so the picker behaves the same regardless of where it's mounted.
  const [resourceTags, setResourceTags] = useState<Array<{ tag: { id: string; name: string; color?: string } }>>([]);
  const [resourceId, setResourceId] = useState<string | null>(explicitResourceId ?? null);
  const [allTags, setAllTags] = useState<Tag[]>([]);

  useEffect(() => {
    fetchAllTags().then(setAllTags).catch(() => {});
  }, []);

  useEffect(() => {
    // Upload path: resourceId is known up front — bind tags to it directly.
    if (explicitResourceId) {
      setResourceId(explicitResourceId);
      setResourceTags([]);
      let cancelled = false;
      (async () => {
        try {
          const tags = await fetchResourceTags(explicitResourceId);
          if (!cancelled) setResourceTags(tags);
        } catch (err) {
          console.error('Failed to load resource tags:', err);
        }
      })();
      return () => { cancelled = true; };
    }
    // Download path: resolve the owning resource from the parsed_media id.
    if (!video?.id) return;
    setResourceTags([]);
    setResourceId(null);
    let cancelled = false;
    (async () => {
      const supabase = getSupabaseClient();
      if (!supabase) return;
      const { data: resource } = await supabase
        .from('resources')
        .select('id')
        .eq('media_id', video.id)
        .limit(1)
        .maybeSingle();
      if (cancelled || !resource) return;
      const resId = String(resource.id);
      if (!cancelled) setResourceId(resId);
      try {
        const tags = await fetchResourceTags(resId);
        if (!cancelled) setResourceTags(tags);
      } catch (err) {
        console.error('Failed to load resource tags:', err);
      }
    })();
    return () => { cancelled = true; };
  }, [video?.id, explicitResourceId]);

  const handleAddTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await addResourceTag(resourceId, tagId);
      const updated = await fetchResourceTags(resourceId);
      setResourceTags(updated);
    } catch (err) {
      console.error('Failed to add tag:', err);
    }
  }, [resourceId]);

  const handleRemoveTag = useCallback(async (tagId: string) => {
    if (!resourceId) return;
    try {
      await removeResourceTag(resourceId, tagId);
      setResourceTags(prev => prev.filter(t => String(t.tag?.id) !== tagId));
    } catch (err) {
      console.error('Failed to remove tag:', err);
    }
  }, [resourceId]);

  const handleCreateTag = useCallback(async (name: string, color: string) => {
    try {
      const tag = await createTag({ name, color, type: 'user' });
      setAllTags(prev => [...prev, tag]);
      return tag;
    } catch (err) {
      console.error('Failed to create tag:', err);
      return null;
    }
  }, []);

  return (
    <div className="audio-side">
      {/* Cover — Music glyph behind the image so a 404 reveals the icon. When
          onCoverClick is set (uploaded audio) the cover is click-to-replace. */}
      <div
        className={`audio-cover${onCoverClick ? ' cursor-pointer' : ''}`}
        onClick={onCoverClick}
        title={onCoverClick ? 'Change cover' : undefined}
        role={onCoverClick ? 'button' : undefined}
      >
        <Music size={44} className="text-content-3 col-start-1 row-start-1" />
        {coverUrl && (
          <img
            src={coverUrl}
            alt=""
            className="col-start-1 row-start-1 w-full h-full object-cover"
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
          />
        )}
      </div>

      <div className="audio-song">{title}</div>
      {author && <div className="audio-artist">@{author}</div>}

      {/* Stats — comment / share / collect, colored per the mock. Download audio
          only; uploaded audio has no parsed_media social stats so the row is
          omitted (the stack still reads correctly without it). */}
      {video && (
        <div className="audio-ovr">
          <span className="c-comment"><MessageCircle size={13} /> {formatNumber(video.comment_count)}</span>
          <span className="c-share"><Share2 size={13} /> {formatNumber(video.share_count)}</span>
          <span className="c-collect"><Bookmark size={13} /> {formatNumber(video.favorite_count)}</span>
        </div>
      )}

      {/* Rating — interactive when a setter is provided (resource-backed), else a
          read-only dim row so the stack still matches the mock visually. */}
      <div className="audio-stars">
        {onRatingChange ? (
          <RatingStars value={rating || 0} onChange={onRatingChange} size={14} />
        ) : (
          <div className="flex items-center gap-0.5" aria-hidden="true">
            {[1, 2, 3, 4, 5].map((star) => (
              <Star key={star} size={14} className="text-content-4" />
            ))}
          </div>
        )}
      </div>

      {/* Tags — the EXACT picker MediaCard uses (EagleTagPicker, bare variant) so
          assign / unassign / create all keep working. */}
      <div className="audio-tagrow">
        <EagleTagPicker
          variant="bare"
          assignedTags={resourceTags.map(item => item.tag).filter((t): t is Tag => !!t)}
          allTags={allTags}
          onAdd={handleAddTag}
          onRemove={handleRemoveTag}
          onCreate={handleCreateTag}
        />
      </div>
    </div>
  );
};
