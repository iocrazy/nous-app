import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  AlertCircle, AlertTriangle, ArrowLeftRight, Bookmark, Calendar, Check, Folder,
  Images, ListOrdered, Loader2, MapPin, Plus, Radio, Search, Send, Sparkles, TrendingUp, X,
} from 'lucide-react';
import {
  createPublishTask, listAccounts, listGeneratedVideos, listLibraryMedia,
  promoteGeneratedVideo, GeneratedVideo,
} from '../../services/distributionService';
import {
  uploadResource, getGalleryItems, getResourceCoverUrl, GALLERY_MIME,
} from '../../services/resourceService';
import {
  addResourceTag, createTag, removeResourceTag,
} from '../../services/unifiedTagService';
import { TO_PUBLISH_TAG_NAME, findToPublishTagId } from '../../services/toPublishService';
import { SocialAccount, LibraryVideo } from '../../types';
import { useToast } from '../Toast';
import { useWorkspaceScope } from '../../hooks/useWorkspaceScope';
import { PageHeader } from '../layout/PageHeader';
import './distribution-v4.css';

type Visibility = 'public' | 'friends' | 'private';
type Mode = 'broadcast' | 'one_to_one';
type Channel = 'official' | 'h5';
type Orientation = 'vertical' | 'horizontal';
type ContentKind = 'video' | 'images';

const VIS: Visibility[] = ['public', 'friends', 'private'];
const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
};

// The "To Publish" well-known tag (name, lookup, lazy-create) now lives in
// services/toPublishService.ts so the Resources context-menu "Mark to publish"
// action and this picker filter share one source of truth.

// Suggested topics shown under the composer. Clicking one adds it like any
// typed topic (they map to Douyin hashtags — # + word).
const TRENDING_TOPICS = ['goldenhour', 'cityscape', '4k'];
// Mirrors the backend schema bounds (normalize_topics): ≤20 tags, ≤50 chars.
const MAX_TOPICS = 20;
const MAX_TOPIC_LEN = 50;
// Cap concurrent image uploads so a large multi-select can't open dozens of
// parallel requests at once — pick order is preserved regardless of timing.
const UPLOAD_CONCURRENCY = 3;

// Deterministic gradient pick per account id — keeps avatars visually
// distinct without needing per-user color config.
const AVA_GRADIENTS = [
  'linear-gradient(135deg,#0ea5e9,#6366f1)',
  'linear-gradient(135deg,#8b5cf6,#ec4899)',
  'linear-gradient(135deg,#f97316,#ef4444)',
  'linear-gradient(135deg,#10b981,#0ea5e9)',
  'linear-gradient(135deg,#f59e0b,#ec4899)',
];
const gradientFor = (id: string): string => {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return AVA_GRADIENTS[h % AVA_GRADIENTS.length];
};

const PLATFORM_BADGE: Record<string, { bg: string; icon: React.ReactNode }> = {
  douyin: {
    bg: '#000',
    icon: (
      <svg viewBox="0 0 24 24" fill="#fff">
        <path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 1 1-1.77-2.45V9.79a5.76 5.76 0 1 0 4.86 5.69V9.05a7.35 7.35 0 0 0 4.3 1.38V7.3a4.28 4.28 0 0 1-3.24-1.48Z" />
      </svg>
    ),
  },
  kuaishou: {
    bg: '#FF4906',
    icon: <svg viewBox="0 0 24 24" fill="#fff"><path d="m10 8 6 4-6 4Z" /></svg>,
  },
  xiaohongshu: {
    bg: '#FE2C55',
    icon: <svg viewBox="0 0 24 24" fill="#fff"><circle cx="12" cy="12" r="5" /></svg>,
  },
};

const HeartIcon: React.FC = () => (
  <svg viewBox="0 0 24 24"><path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z" /></svg>
);
const CommentIcon: React.FC = () => (
  <svg viewBox="0 0 24 24"><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" /></svg>
);
const ShareGlyph: React.FC = () => (
  <svg viewBox="0 0 24 24"><path d="m22 2-7 20-4-9-9-4Z" /></svg>
);
const MusicIcon: React.FC = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18V5l12-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="18" cy="16" r="3" />
  </svg>
);

export const PublishPage: React.FC = () => {
  const { t } = useTranslation();
  const { addToast } = useToast();
  const navigate = useNavigate();
  const { scopeId } = useWorkspaceScope();

  const [contentType, setContentType] = useState<ContentKind>('video');
  const [videos, setVideos] = useState<LibraryVideo[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  // selectedVideos holds media resource ids in PICK ORDER — for images that
  // order IS the gallery order sent to the note.
  const [selectedVideos, setSelectedVideos] = useState<string[]>([]);
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [topics, setTopics] = useState<string[]>([]);
  const [topicInput, setTopicInput] = useState('');
  const [topicInputOpen, setTopicInputOpen] = useState(false);
  const [visibility, setVisibility] = useState<Visibility>('public');
  const [aiContent, setAiContent] = useState(false);
  const [allowDownload, setAllowDownload] = useState(true);
  const [mode, setMode] = useState<Mode>('broadcast');
  const [channel, setChannel] = useState<Channel>('h5');
  const [orientation, setOrientation] = useState<Orientation>('vertical');
  const [customizeOpen, setCustomizeOpen] = useState<Record<string, boolean>>({});
  const [accountConfigs, setAccountConfigs] = useState<Record<string, { title: string }>>({});
  const [submitting, setSubmitting] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerQuery, setPickerQuery] = useState('');
  const [pickerTab, setPickerTab] = useState<'library' | 'generated'>('library');
  const [toPublishOnly, setToPublishOnly] = useState(false);
  const [generated, setGenerated] = useState<GeneratedVideo[]>([]);
  // genId → promoted resource id (seeded from the backlink, extended on pick).
  const [genResourceIds, setGenResourceIds] = useState<Record<string, string>>({});
  const [toPublishTagId, setToPublishTagId] = useState<string | null>(null);
  const [markedIds, setMarkedIds] = useState<Set<string>>(new Set());
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Gallery cards expand into their child images on pick. Cache the ordered
  // child ids per gallery so the toggle-off path can remove the exact group
  // without re-fetching. `expandingGalleryId` drives the card busy state.
  const [galleryChildren, setGalleryChildren] = useState<Record<string, string[]>>({});
  const [expandingGalleryId, setExpandingGalleryId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const mediaType = contentType === 'images' ? 'image' : 'video';
    try {
      // Generated media is video-only today — skip it entirely in images mode.
      const [v, a, gen] = await Promise.all([
        listLibraryMedia(scopeId, { mediaType }),
        listAccounts(),
        contentType === 'images' ? Promise.resolve([]) : listGeneratedVideos(),
      ]);
      setVideos(v);
      setAccounts(a);
      setGenerated(gen);
      setGenResourceIds(Object.fromEntries(
        gen.filter((g) => g.promoted_resource_id).map((g) => [g.id, g.promoted_resource_id as string]),
      ));
    } catch (err) {
      console.error('distribution: publish page load failed', err);
      addToast(t('distribution.publish.loadFailed', 'Failed to load publish data'), 'error');
    }
    // "To publish" mark state — non-fatal side channel; failures leave the
    // filter empty but never block the page. Scoped to the current media type
    // so marks line up with the listed media.
    try {
      const tagId = await findToPublishTagId();
      if (tagId) {
        setToPublishTagId(tagId);
        const marked = await listLibraryMedia(scopeId, { tagId, mediaType });
        setMarkedIds(new Set(marked.map((m) => m.id)));
      }
    } catch (err) {
      console.error('distribution: load to-publish marks failed', err);
    }
  }, [addToast, t, scopeId, contentType]);

  useEffect(() => { void load(); }, [load]);

  // Close the library picker on Escape while it is open.
  useEffect(() => {
    if (!pickerOpen) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setPickerOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [pickerOpen]);

  const toggle = (list: string[], id: string): string[] =>
    (list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);

  const removeVideo = (id: string) =>
    setSelectedVideos((s) => s.filter((x) => x !== id));

  const isImages = contentType === 'images';

  // Switch content type: clears the selection (video ids ≠ image ids), resets
  // the picker to the Library tab (Generated is video-only), and pins images
  // to broadcast (a note is one post per account — never round-robin split).
  const onContentTypeChange = (kind: ContentKind) => {
    if (kind === contentType) return;
    setContentType(kind);
    setSelectedVideos([]);
    setPickerTab('library');
    if (kind === 'images') setMode('broadcast');
  };

  // ── Topics (Douyin hashtags) ──
  // Add one bare tag (strip leading '#'), enforcing the same bounds as the
  // backend schema and de-duplicating case-insensitively. Immutable update.
  const addTopic = useCallback((raw: string) => {
    const tag = raw.replace(/^#+/, '').trim();
    if (!tag || tag.length > MAX_TOPIC_LEN) return;
    setTopics((prev) => (
      prev.length >= MAX_TOPICS || prev.some((x) => x.toLowerCase() === tag.toLowerCase())
        ? prev
        : [...prev, tag]
    ));
  }, []);

  // Commit the input: split on comma/whitespace so a pasted "a, b c" adds all.
  const commitTopicInput = useCallback(() => {
    topicInput
      .split(/[,\s]+/)
      .map((s) => s.replace(/^#+/, '').trim())
      .filter(Boolean)
      .forEach((p) => addTopic(p));
    setTopicInput('');
  }, [topicInput, addTopic]);

  const removeTopic = (tag: string) =>
    setTopics((prev) => prev.filter((x) => x !== tag));

  const onTopicKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // IME composition guard (#1453 pattern): never commit mid-composition.
    if (e.nativeEvent.isComposing) return;
    if (e.key === 'Enter' || e.key === ',' || (e.key === ' ' && topicInput.trim())) {
      e.preventDefault();
      commitTopicInput();
    } else if (e.key === 'Backspace' && !topicInput && topics.length) {
      e.preventDefault();
      setTopics((prev) => prev.slice(0, -1));
    }
  };

  // Only the videos the user actually picked are shown as content thumbs —
  // never the whole Library. Resolve ids → video rows, dropping any that no
  // longer exist in the loaded Library list.
  const selectedVideoObjs = useMemo(
    () => selectedVideos
      .map((id) => videos.find((v) => v.id === id))
      .filter((v): v is LibraryVideo => Boolean(v)),
    [selectedVideos, videos],
  );

  const pickerResults = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    const base = toPublishOnly ? videos.filter((v) => markedIds.has(v.id)) : videos;
    return q ? base.filter((v) => v.filename.toLowerCase().includes(q)) : base;
  }, [videos, pickerQuery, toPublishOnly, markedIds]);

  const generatedResults = useMemo(() => {
    const q = pickerQuery.trim().toLowerCase();
    return q ? generated.filter((g) => g.name.toLowerCase().includes(q)) : generated;
  }, [generated, pickerQuery]);

  // Toggle the "To Publish" mark on a Library video (creates the well-known
  // tag on first use). Optimistic; reverts by reloading marks on failure.
  const onToggleMark = async (videoId: string) => {
    const wasMarked = markedIds.has(videoId);
    setMarkedIds((prev) => {
      const next = new Set(prev);
      if (wasMarked) next.delete(videoId); else next.add(videoId);
      return next;
    });
    try {
      let tagId = toPublishTagId;
      if (!tagId) {
        const created = await createTag({ name: TO_PUBLISH_TAG_NAME, color: '#6366f1' });
        tagId = created.id;
        setToPublishTagId(tagId);
      }
      if (wasMarked) await removeResourceTag(videoId, tagId);
      else await addResourceTag(videoId, tagId);
    } catch (err) {
      console.error('distribution: toggle to-publish mark failed', err);
      addToast(t('distribution.publish.markUpdateFailed', 'Could not update publish mark'), 'error');
      setMarkedIds((prev) => {
        const next = new Set(prev);
        if (wasMarked) next.add(videoId); else next.delete(videoId);
        return next;
      });
    }
  };

  // Insert a synthetic Library row for a promoted generation so the selected
  // thumbs can resolve it even when the resource lives outside the current
  // workspace listing (promote targets the personal scope).
  const ensureVideoRow = (resourceId: string, name: string, thumbnailUrl: string | null = null) =>
    setVideos((prev) => (prev.some((v) => v.id === resourceId)
      ? prev
      : [{ id: resourceId, filename: name, thumbnail_url: thumbnailUrl }, ...prev]));

  // Pick a generated video: promote it into the Library on first pick
  // (idempotent server-side via the promoted_resource_id backlink), then
  // toggle the resulting resource id like any other selection.
  const onPickGenerated = async (g: GeneratedVideo) => {
    const fallbackName = g.name || t('distribution.publish.generatedUntitled', 'Generated video');
    const known = genResourceIds[g.id];
    if (known) {
      ensureVideoRow(known, fallbackName);
      setSelectedVideos((s) => toggle(s, known));
      return;
    }
    if (promotingId) return;
    setPromotingId(g.id);
    try {
      const resourceId = await promoteGeneratedVideo(g.id);
      setGenResourceIds((prev) => ({ ...prev, [g.id]: resourceId }));
      ensureVideoRow(resourceId, fallbackName);
      setSelectedVideos((s) => (s.includes(resourceId) ? s : [...s, resourceId]));
    } catch (err) {
      console.error('distribution: promote generated video failed', err);
      addToast(t('distribution.publish.promoteFailed', 'Could not add generated video'), 'error');
    } finally {
      setPromotingId(null);
    }
  };

  // ── Gallery cards (Images mode) ──
  // A gallery is one Library row that stands in for an ordered group of child
  // images. The gallery id itself is NEVER published — picking a gallery
  // expands it into its child image ids (added to `selectedVideos` in position
  // order) so the publish payload carries only image resource ids and the
  // downstream workflow needs zero changes. Toggle semantics: every child
  // already selected → remove the whole group; otherwise add the missing ones.
  const isGalleryRow = useCallback(
    (v: LibraryVideo): boolean => v.mime_type === GALLERY_MIME,
    [],
  );

  const onPickGallery = async (v: LibraryVideo) => {
    if (expandingGalleryId) return;
    let childIds = galleryChildren[v.id];
    if (!childIds) {
      setExpandingGalleryId(v.id);
      try {
        const children = await getGalleryItems(v.id);
        // Defensive sort — never trust the API to return position order.
        const ordered = [...children].sort((a, b) => a.position - b.position);
        childIds = ordered.map((c) => String(c.id));
        setGalleryChildren((prev) => ({ ...prev, [v.id]: childIds as string[] }));
        // Gallery children are hidden from the picker list (backend NOT EXISTS),
        // so seed synthetic rows carrying each child's own cover for the
        // selected-thumbs strip.
        ordered.forEach((c) => {
          const thumb = c.thumbnail_path ? getResourceCoverUrl(String(c.id)) : null;
          ensureVideoRow(String(c.id), c.filename || 'Image', thumb);
        });
      } catch (err) {
        console.error('distribution: expand gallery failed', err);
        addToast(t('distribution.publish.galleryExpandFailed', 'Could not open gallery'), 'error');
        return;
      } finally {
        setExpandingGalleryId(null);
      }
    }
    if (!childIds || childIds.length === 0) {
      addToast(t('distribution.publish.galleryEmpty', 'This gallery has no images'), 'info');
      return;
    }
    const ids = childIds;
    setSelectedVideos((s) => {
      const allPresent = ids.every((id) => s.includes(id));
      if (allPresent) return s.filter((id) => !ids.includes(id));
      const set = new Set(s);
      return [...s, ...ids.filter((id) => !set.has(id))];
    });
  };

  // ── Inline image upload (Images mode only) ──
  // Open the hidden file input. Guarded while a batch is in flight.
  const onUploadClick = () => {
    if (uploading) return;
    fileInputRef.current?.click();
  };

  // Upload one file into the current scope's My Uploads root (source_type
  // 'upload' is the endpoint default). Returns the new resource row, or null
  // so a single failure never aborts the rest of the batch.
  const uploadOne = async (file: File): Promise<{ id: string; name: string } | null> => {
    try {
      const r = await uploadResource(file, scopeId);
      return { id: r.id, name: r.filename };
    } catch (err) {
      console.error('distribution: inline image upload failed', err);
      return null;
    }
  };

  // Upload the picked images (concurrency ≤ UPLOAD_CONCURRENCY), then insert the
  // successes into the Library rows and auto-select them IN PICK ORDER — the
  // result slot is keyed by the original index, so completion timing can't
  // reorder the gallery. Aggregates failures into a single toast.
  const onFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // Reset so re-picking the same file still fires a change event.
    e.target.value = '';
    if (files.length === 0) return;
    setUploading(true);
    try {
      const results: Array<{ id: string; name: string } | null> = new Array(files.length).fill(null);
      let cursor = 0;
      const worker = async () => {
        while (cursor < files.length) {
          const idx = cursor;
          cursor += 1;
          results[idx] = await uploadOne(files[idx]);
        }
      };
      await Promise.all(
        Array.from({ length: Math.min(UPLOAD_CONCURRENCY, files.length) }, worker),
      );
      let failures = 0;
      results.forEach((res) => {
        if (!res) { failures += 1; return; }
        ensureVideoRow(res.id, res.name);
        setSelectedVideos((s) => (s.includes(res.id) ? s : [...s, res.id]));
      });
      if (failures > 0) {
        addToast(
          t('distribution.publish.uploadFailed', '{{n}} image(s) failed to upload', { n: failures }),
          'error',
        );
      }
    } finally {
      setUploading(false);
    }
  };

  const canPublish = useMemo(
    () => selectedVideos.length > 0 && selectedAccounts.length > 0 && title.trim().length > 0,
    [selectedVideos, selectedAccounts, title],
  );

  const postsBroadcast = selectedVideos.length * selectedAccounts.length;
  const postsOneToOne = selectedVideos.length > 0 ? selectedAccounts.length : 0;
  // Images = ONE note per account carrying every picked image, so the post
  // count is just the account count (the gallery is never split).
  const totalPosts = isImages
    ? (selectedVideos.length > 0 ? selectedAccounts.length : 0)
    : (mode === 'broadcast' ? postsBroadcast : postsOneToOne);

  const firstSelectedAccount = useMemo(
    () => accounts.find((a) => a.id === selectedAccounts[0]),
    [accounts, selectedAccounts],
  );
  const previewHandle = firstSelectedAccount?.username ?? 'yourhandle';

  const visLabel = (v: Visibility): string => {
    if (v === 'public') return t('distribution.publish.vis_public', 'Public');
    if (v === 'private') return t('distribution.publish.vis_private', 'Private');
    return t('distribution.publish.visFriends', 'Friends');
  };

  const onToggleAccount = (accountId: string, expired: boolean) => {
    if (expired) return;
    setSelectedAccounts((s) => toggle(s, accountId));
  };

  const onAccountTitleChange = (accountId: string, value: string) => {
    setAccountConfigs((prev) => ({ ...prev, [accountId]: { title: value } }));
  };

  const onPublish = async () => {
    if (!canPublish || submitting) return;
    setSubmitting(true);
    try {
      const accountConfigsPayload = Object.fromEntries(
        Object.entries(accountConfigs)
          .filter(([id, cfg]) => selectedAccounts.includes(id) && cfg.title.trim().length > 0)
          .map(([id, cfg]) => [id, { title: cfg.title.trim() }]),
      );
      await createPublishTask({
        content_type: contentType,
        resource_ids: selectedVideos,
        title: title.trim(),
        description: description.trim() || undefined,
        topics: topics.length ? topics : undefined,
        visibility,
        ai_content: aiContent,
        allow_download: allowDownload,
        // Images always broadcast (one note per account); force it so a stale
        // one_to_one selection can't leak into the payload.
        distribution_mode: isImages ? 'broadcast' : mode,
        channel,
        account_ids: selectedAccounts,
        account_configs: Object.keys(accountConfigsPayload).length ? accountConfigsPayload : undefined,
      });
      addToast(t('distribution.publish.queued', 'Publish task created'), 'success');
      navigate('../records');
    } catch (err) {
      console.error('distribution: create publish task failed', err);
      addToast(t('distribution.publish.failed', 'Could not create publish task'), 'error');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dist-v4">
      <PageHeader
        level="content"
        title={t('distribution.publish.title', 'Publish')}
        subtitle={t('distribution.publish.subtitle', 'Send library media to your connected accounts.')}
      />

      <div className="stepper" aria-hidden="true">
        <span className="step done">
          <span className="n"><Check size={11} /></span>
          {t('distribution.publish.content', 'Content')}
        </span>
        <span className="step-line done" />
        <span className="step cur"><span className="n">2</span>{t('distribution.publish.stepDetailsAccounts', 'Details & accounts')}</span>
        <span className="step-line" />
        <span className="step"><span className="n">3</span>{t('distribution.publish.stepDone', 'Done')}</span>
      </div>

      <div className="pub-cols">
        {/* ── left: form ── */}
        <div className="pub-form">
          <div className="fcard">
            <h4>
              {t('distribution.publish.content', 'Content')}
              <span className="aux">
                {isImages
                  ? t('distribution.publish.imagesSelectedCount', 'Images · {{n}} selected', { n: selectedVideos.length })
                  : t('distribution.publish.videoSelectedCount', 'Video · {{n}} selected', { n: selectedVideos.length })}
              </span>
            </h4>
            <div className="seg" style={{ marginBottom: 10 }} role="tablist" aria-label={t('distribution.publish.contentType', 'Content type')}>
              <button
                type="button"
                role="tab"
                aria-selected={!isImages}
                className={!isImages ? 'on' : ''}
                onClick={() => onContentTypeChange('video')}
              >
                {t('distribution.publish.contentTypeVideo', 'Video')}
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={isImages}
                className={isImages ? 'on' : ''}
                onClick={() => onContentTypeChange('images')}
              >
                {t('distribution.publish.contentTypeImages', 'Images')}
              </button>
            </div>
            <div className="seg">
              <button type="button" className="on">{t('distribution.publish.fromLibrary', 'From Library')}</button>
              {isImages ? (
                <button
                  type="button"
                  disabled={uploading}
                  aria-busy={uploading}
                  onClick={onUploadClick}
                >
                  {uploading ? (
                    <>
                      <Loader2 size={12} className="animate-spin" />
                      {t('distribution.publish.uploading', 'Uploading…')}
                    </>
                  ) : (
                    t('distribution.publish.upload', 'Upload')
                  )}
                </button>
              ) : (
                // Video uploads stay locked — large files need a resumable path.
                <button type="button" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.upload', 'Upload')}</button>
              )}
            </div>
            {isImages && (
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                aria-label={t('distribution.publish.uploadImagesAria', 'Upload images')}
                onChange={onFilesSelected}
              />
            )}
            <div className="thumbs">
              {selectedVideoObjs.map((v, idx) => {
                const hasImg = Boolean(v.thumbnail_url);
                return (
                  <div
                    key={v.id}
                    aria-label={v.filename}
                    title={v.filename}
                    className={`thumb ${hasImg ? '' : idx % 2 === 0 ? 't1' : 't2'}`}
                    style={hasImg ? { backgroundImage: `url(${v.thumbnail_url})` } : undefined}
                  >
                    <button
                      type="button"
                      className="rm"
                      aria-label={t('distribution.publish.removeVideo', 'Remove {{name}}', { name: v.filename })}
                      onClick={() => removeVideo(v.id)}
                    >
                      ×
                    </button>
                    {isImages ? (
                      <span
                        className="ord"
                        aria-label={t('distribution.publish.imageOrder', 'Image {{n}}', { n: idx + 1 })}
                      >
                        {idx + 1}
                      </span>
                    ) : (
                      <span className="play">
                        <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                      </span>
                    )}
                  </div>
                );
              })}
              <button
                type="button"
                className="thumb add"
                onClick={() => { setPickerQuery(''); setPickerOpen(true); }}
              >
                <Plus size={16} />
                {t('distribution.publish.addFromLibrary', 'Add from Library')}
              </button>
            </div>
          </div>

          <div className="fcard">
            <h4>{t('distribution.publish.cover', 'Cover')} <span className="aux">{t('distribution.publish.notSetYet', 'Not set yet')}</span></h4>
            <div className="cover-wrap">
              <div className="cover-slots">
                <div className="cover-slot v is-soon" aria-disabled="true">
                  <span className="soon-tag">{t('distribution.publish.soon', 'Soon')}</span>
                  <Sparkles />
                  {t('distribution.publish.vertical34', 'Vertical 3:4')}
                </div>
                <div className="cover-slot h is-soon" aria-disabled="true">
                  <span className="soon-tag">{t('distribution.publish.soon', 'Soon')}</span>
                  <Sparkles />
                  {t('distribution.publish.horizontal43', 'Horizontal 4:3')}
                </div>
              </div>
              <div className="cover-ai">
                <div className="head">
                  <b><Sparkles size={14} />{t('distribution.publish.aiCoversCanvas', 'AI covers · Canvas')}</b>
                  <a href="#cover-studio" aria-disabled="true" onClick={(e) => e.preventDefault()}>
                    {t('distribution.publish.openCoverStudio', 'Open Cover Studio')}
                  </a>
                </div>
                <div className="cover-cands">
                  <div className="cand" style={{ background: 'linear-gradient(170deg,#46346e,#23375f 55%,#132c47)' }} />
                  <div className="cand" style={{ background: 'linear-gradient(170deg,#6e3446,#4c2b5e 60%,#1e1e3a)' }} />
                  <div className="cand" style={{ background: 'linear-gradient(200deg,#0f3a4d,#46346e 70%,#1e1e3a)' }} />
                </div>
                <div className="foot">{t('distribution.publish.coverGenDesc', 'Generates candidates from a video frame + your title.')}</div>
                <div className="d4-note">{t('distribution.publish.comingInD4', 'Coming in D4')}</div>
              </div>
            </div>
          </div>

          <div className="fcard">
            <h4>{t('distribution.publish.titleLabel', 'Title')} <span className="aux">{title.length} / 500</span></h4>
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              maxLength={500}
              placeholder={t('distribution.publish.titlePlaceholder', 'Add a title')}
            />
            <h4 style={{ marginTop: 15 }}>{t('distribution.publish.descriptionLabel', 'Description')} <span className="aux">{description.length} / 1000</span></h4>
            <textarea
              className="input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={1000}
              rows={3}
              style={{ minHeight: 64 }}
              placeholder={t('distribution.publish.descPlaceholder', 'Add a description')}
            />
            <div className="topics">
              <button
                type="button"
                className={`chip ${topicInputOpen ? 'chip-indigo' : 'chip-mute'}`}
                aria-pressed={topicInputOpen}
                aria-expanded={topicInputOpen}
                onClick={() => setTopicInputOpen((v) => !v)}
              >
                {t('distribution.publish.topicChip', '# Topic')}
              </button>
              <span
                className="chip chip-mute chip-soon"
                aria-disabled="true"
                title={t('distribution.publish.mentionSoon', 'Mentions need the recipient’s open_id — coming later')}
              >
                {t('distribution.publish.mentionChip', '@ Mention')}
                <em className="soon">{t('distribution.publish.soon', 'Soon')}</em>
              </span>
              {topics.map((tag) => (
                <span key={tag} className="chip chip-topic">
                  #{tag}
                  <button
                    type="button"
                    className="chip-x"
                    aria-label={t('distribution.publish.removeTopic', 'Remove topic {{tag}}', { tag })}
                    onClick={() => removeTopic(tag)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            {topicInputOpen && (
              <div className="topic-input-row" style={{ marginTop: 7 }}>
                <input
                  className="input"
                  value={topicInput}
                  maxLength={MAX_TOPIC_LEN}
                  aria-label={t('distribution.publish.topicInputAria', 'Add a topic')}
                  placeholder={t('distribution.publish.topicInputPlaceholder', 'Type a topic, press Enter (comma or space also adds)')}
                  onChange={(e) => setTopicInput(e.target.value)}
                  onKeyDown={onTopicKeyDown}
                  onBlur={commitTopicInput}
                />
              </div>
            )}
            <div className="topics" style={{ marginTop: 7 }}>
              <span className="trending-label">{t('distribution.publish.trending', 'Trending')}</span>
              {TRENDING_TOPICS.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  className="chip chip-mute"
                  onClick={() => addTopic(tag)}
                >
                  #{tag}
                </button>
              ))}
            </div>
          </div>

          <div className="fcard">
            <div className="frow">
              <div className="lbl"><b>{t('distribution.publish.visibility', 'Visibility')}</b></div>
              <div className="seg">
                {VIS.map((v) => (
                  <button key={v} type="button" className={visibility === v ? 'on' : ''} onClick={() => setVisibility(v)}>
                    {visLabel(v)}
                  </button>
                ))}
              </div>
            </div>
            <div className="frow">
              <div className="lbl">
                <b>{t('distribution.publish.aiContent', 'AI-generated content')}</b>
                <span>{t('distribution.publish.aiContentDesc', 'Saved with the task — Douyin requires setting the AI label in-app.')}</span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={aiContent}
                aria-label={t('distribution.publish.aiContent', 'AI-generated content')}
                className={`toggle ${aiContent ? 'on' : ''}`}
                onClick={() => setAiContent((v) => !v)}
              />
            </div>
            <div className="frow">
              <div className="lbl">
                <b>{t('distribution.publish.allowDownloadsLabel', 'Allow downloads')}</b>
                <span>{t('distribution.publish.allowDownloadsDesc', 'Viewers can save the video to their device')}</span>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={allowDownload}
                aria-label={t('distribution.publish.allowDownloadsLabel', 'Allow downloads')}
                className={`toggle ${allowDownload ? 'on' : ''}`}
                onClick={() => setAllowDownload((v) => !v)}
              />
            </div>
            <div className="frow">
              <div className="lbl"><b>{t('distribution.publish.publishTime', 'Publish time')}</b></div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div className="seg">
                  <button type="button" className="on">{t('distribution.publish.scheduleNow', 'Now')}</button>
                  <button type="button" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.schedule', 'Schedule')}</button>
                </div>
                <span className="sched-input"><Calendar />{t('distribution.publish.notScheduled', 'Not scheduled')}</span>
              </div>
            </div>
          </div>

          <div className="fcard">
            <h4>{t('distribution.publish.moreOptions', 'More options')}</h4>
            <div className="opt-row">
              <Folder />
              <span className="ol">{t('distribution.publish.collection', 'Collection')}</span>
              <span className="oa">{t('distribution.publish.change', 'Change')}</span>
            </div>
            <div className="opt-row">
              <MapPin />
              <span className="ol">{t('distribution.publish.location', 'Location')}</span>
              <span className="oa">{t('distribution.publish.add', 'Add')}</span>
            </div>
            <div className="opt-row">
              <TrendingUp />
              <span className="ol">{t('distribution.publish.trendingTopic', 'Trending topic')}</span>
              <span className="ov">{t('distribution.publish.trendingTopicDesc', 'Link a rising topic for extra reach')}</span>
              <span className="oa">{t('distribution.publish.link', 'Link')}</span>
            </div>
            <div className="opt-row">
              <ListOrdered />
              <span className="ol">{t('distribution.publish.chapters', 'Chapters')}</span>
              <span className="ov">{t('distribution.publish.chaptersDesc', 'Where the platform supports them')}</span>
              <span className="oa">{t('distribution.publish.add', 'Add')}</span>
            </div>
          </div>

          {!isImages && (
            <div className="fcard">
              <h4>
                {t('distribution.publish.distributionMode', 'Distribution mode')}
                <span className="aux">
                  {t('distribution.publish.videosAccountsCount', '{{v}} videos × {{a}} accounts', { v: selectedVideos.length, a: selectedAccounts.length })}
                </span>
              </h4>
              <div className="mode-cards">
                <button type="button" className={`mode ${mode === 'broadcast' ? 'on' : ''}`} onClick={() => setMode('broadcast')}>
                  <b><Radio /> {t('distribution.publish.mode_broadcast', 'Broadcast')}</b>
                  <span>{t('distribution.publish.broadcastDesc', 'Every account posts every video — {{n}} posts total.', { n: postsBroadcast })}</span>
                </button>
                <button type="button" className={`mode ${mode === 'one_to_one' ? 'on' : ''}`} onClick={() => setMode('one_to_one')}>
                  <b><ArrowLeftRight /> {t('distribution.publish.mode_one_to_one', 'One-to-one')}</b>
                  <span>{t('distribution.publish.oneToOneDesc', 'Videos are assigned round-robin — {{n}} posts total.', { n: postsOneToOne })}</span>
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ── right: rail ── */}
        <div className="pub-rail">
          <div className="phone-card">
            <div className="bar">
              <h4>{t('distribution.publish.livePreview', 'Live preview')}</h4>
              <div className="seg">
                <button type="button" className={orientation === 'vertical' ? 'on' : ''} onClick={() => setOrientation('vertical')}>{t('distribution.publish.vertical', 'Vertical')}</button>
                <button type="button" className={orientation === 'horizontal' ? 'on' : ''} onClick={() => setOrientation('horizontal')}>{t('distribution.publish.horizontal', 'Horizontal')}</button>
              </div>
            </div>
            <div className="phone">
              <span className="notch" />
              <div
                className="scene"
                aria-label={isImages
                  ? t('distribution.publish.imagePreviewAria', 'First image preview — gallery post')
                  : undefined}
                style={isImages && selectedVideoObjs[0]?.thumbnail_url
                  ? { backgroundImage: `url(${selectedVideoObjs[0].thumbnail_url})`, backgroundSize: 'cover', backgroundPosition: 'center' }
                  : undefined}
              />
              <div className="shade" />
              <div className="ui">
                <div className="tabs"><span>{t('distribution.publish.following', 'Following')}</span><span className="cur-t">{t('distribution.publish.forYou', 'For You')}</span></div>
                <div className="bottom">
                  <div className="meta">
                    <div className="handle"><span className="a" />@{previewHandle}</div>
                    <div className="cap">{title || t('distribution.publish.titlePlaceholderPreview', 'Your title appears here')}</div>
                    <div className="music"><MusicIcon />{t('distribution.publish.originalSound', 'Original sound · {{handle}}', { handle: previewHandle })}</div>
                  </div>
                  <div className="rail">
                    <span className="act"><span className="ic"><HeartIcon /></span>0</span>
                    <span className="act"><span className="ic"><CommentIcon /></span>0</span>
                    <span className="act"><span className="ic"><ShareGlyph /></span>{t('distribution.publish.shareLabel', 'Share')}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="fcard">
            <h4>
              {t('distribution.publish.publishTo', 'Publish to')}
              <span className="aux">
                {t('distribution.publish.selectedOfTotal', '{{selected}} of {{total}} selected', { selected: selectedAccounts.length, total: accounts.length })}
              </span>
            </h4>
            <div className="seg" style={{ marginBottom: 10 }}>
              <button type="button" className={channel === 'h5' ? 'on' : ''} onClick={() => setChannel('h5')}>{t('distribution.publish.channelH5', 'H5 share')}</button>
              {/* Official API stays locked until the Douyin app clears review —
                  the backend code path is intact and re-enables by dropping
                  `disabled`. Channel is pinned to H5 meanwhile. */}
              <button
                type="button"
                disabled
                title={t('distribution.publish.officialLocked', 'Requires Douyin app review — H5 share only for now')}
              >
                {t('distribution.publish.channel_official', 'Official API')}
              </button>
            </div>

            {accounts.map((a) => {
              const expired = a.status === 'expired';
              const on = selectedAccounts.includes(a.id);
              const badge = PLATFORM_BADGE[a.platform];
              const open = customizeOpen[a.id];
              return (
                <React.Fragment key={a.id}>
                  <div
                    role="checkbox"
                    aria-checked={on}
                    aria-disabled={expired}
                    tabIndex={expired ? -1 : 0}
                    className={`acct-row ${on ? 'sel' : ''} ${expired ? 'dis' : ''}`}
                    onClick={() => onToggleAccount(a.id, expired)}
                    onKeyDown={(e) => {
                      if (!expired && (e.key === 'Enter' || e.key === ' ')) {
                        e.preventDefault();
                        onToggleAccount(a.id, expired);
                      }
                    }}
                  >
                    <span className="ck" />
                    <span className="ava" style={{ background: gradientFor(a.id) }}>
                      {a.username.slice(0, 2).toUpperCase()}
                      {badge && (
                        <span className="pbadge sm" style={{ background: badge.bg }}>{badge.icon}</span>
                      )}
                    </span>
                    <span className="nm">
                      {a.username}
                      <small>
                        {PLATFORM_LABEL[a.platform] ?? a.platform}
                        {' · '}
                        {expired
                          ? t('distribution.publish.expiredReauthorize', 'Expired — reauthorize')
                          : (a.scope_type === 'team' ? t('distribution.teamScope', 'Team') : t('distribution.personalScope', 'Personal'))}
                      </small>
                    </span>
                    {!expired && (
                      <button
                        type="button"
                        className="cust"
                        onClick={(e) => {
                          e.stopPropagation();
                          setCustomizeOpen((s) => ({ ...s, [a.id]: !s[a.id] }));
                        }}
                      >
                        {t('distribution.publish.customize', 'Customize')} {open ? '▾' : '▸'}
                      </button>
                    )}
                  </div>
                  {!expired && open && (
                    <div className="override">
                      <label htmlFor={`override-title-${a.id}`}>{t('distribution.publish.titleForAccount', 'Title for this account')}</label>
                      <input
                        id={`override-title-${a.id}`}
                        className="input"
                        value={accountConfigs[a.id]?.title ?? ''}
                        placeholder={title}
                        onChange={(e) => onAccountTitleChange(a.id, e.target.value)}
                      />
                    </div>
                  )}
                </React.Fragment>
              );
            })}
            {accounts.length === 0 && (
              <p className="text-[12px]" style={{ color: 'var(--content-4)' }}>
                {t('distribution.publish.noAccounts', 'Connect an account first')}
              </p>
            )}
          </div>

          <div className="summary">
            <h4>{t('distribution.publish.summaryHeading', 'Summary')}</h4>
            <div className="line">
              <span>{isImages ? t('distribution.publish.imagesLabel', 'Images') : t('distribution.publish.videosLabel', 'Videos')}</span>
              <b>{selectedVideos.length}</b>
            </div>
            <div className="line"><span>{t('distribution.publish.accountsLabel', 'Accounts')}</span><b>{selectedAccounts.length}</b></div>
            {!isImages && (
              <div className="line"><span>{t('distribution.publish.modeLabel', 'Mode')}</span><b>{mode === 'broadcast' ? t('distribution.publish.mode_broadcast', 'Broadcast') : t('distribution.publish.mode_one_to_one', 'One-to-one')}</b></div>
            )}
            <div className="line total"><span>{t('distribution.publish.postsToCreate', 'Posts to create')}</span><b>{totalPosts}</b></div>

            <div className="check warn">
              <AlertTriangle />
              {t('distribution.publish.coverNotSetWarning', 'Cover Studio coming in D4 — Douyin picks the cover during publish.')}
            </div>
            {canPublish && (
              <div className="check ok">
                <Check strokeWidth={2.5} />
                {t('distribution.publish.lookingGood', 'Title, topics and accounts look good.')}
              </div>
            )}

            <div className="actions">
              <button type="button" className="btn btn-ghost" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.saveDraft', 'Save draft')}</button>
              <button type="button" className="btn btn-solid" disabled={!canPublish || submitting} onClick={onPublish}>
                <Send size={15} /> {t('distribution.publish.publishNow', 'Publish now')}
              </button>
            </div>

            {channel === 'h5' && (
              <div className="handoff">
                <AlertCircle />
                {t('distribution.publish.handoffMsg', 'Douyin personal accounts finish inside the Douyin app — we hand off automatically and track the result here.')}
              </div>
            )}
          </div>
        </div>
      </div>

      {pickerOpen && (
        <div
          className="picker-overlay"
          role="presentation"
          onClick={() => setPickerOpen(false)}
        >
          <div
            className="picker"
            role="dialog"
            aria-modal="true"
            aria-label={t('distribution.publish.pickerTitle', 'Add from Library')}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="picker-head">
              <div>
                <h3>{t('distribution.publish.pickerTitle', 'Add from Library')}</h3>
                <p>{t('distribution.publish.pickerSubtitle', 'Pick videos to include in this publish.')}</p>
              </div>
              <div className="picker-count">
                {t('distribution.publish.pickerSelectedCount', '{{n}} selected', { n: selectedVideos.length })}
              </div>
              <button
                type="button"
                className="picker-close"
                aria-label={t('distribution.publish.pickerClose', 'Close')}
                onClick={() => setPickerOpen(false)}
              >
                <X size={16} />
              </button>
            </div>

            <div className="picker-search">
              <Search size={14} />
              <input
                value={pickerQuery}
                onChange={(e) => setPickerQuery(e.target.value)}
                placeholder={t('distribution.publish.pickerSearch', 'Search videos')}
                aria-label={t('distribution.publish.pickerSearch', 'Search videos')}
              />
            </div>

            <div className="picker-tabs">
              <div className="seg">
                <button
                  type="button"
                  className={pickerTab === 'library' ? 'on' : ''}
                  onClick={() => setPickerTab('library')}
                >
                  {t('distribution.publish.pickerTabLibrary', 'Library')}
                </button>
                {/* Generated media is video-only — hide the tab for images. */}
                {!isImages && (
                  <button
                    type="button"
                    className={pickerTab === 'generated' ? 'on' : ''}
                    onClick={() => setPickerTab('generated')}
                  >
                    {t('distribution.publish.pickerTabGenerated', 'Generated')}
                  </button>
                )}
              </div>
              {pickerTab === 'library' && (
                <button
                  type="button"
                  className={`chip ${toPublishOnly ? 'chip-indigo' : 'chip-mute'}`}
                  aria-pressed={toPublishOnly}
                  onClick={() => setToPublishOnly((v) => !v)}
                >
                  <Bookmark size={11} />
                  {t('distribution.publish.pickerToPublishOnly', 'To publish')}
                </button>
              )}
            </div>

            {pickerTab === 'library' && (
              <div className="picker-grid">
                {pickerResults.map((v) => {
                  // Gallery entity — its own card that expands into child images
                  // on pick. `on` reflects the whole group being selected.
                  if (isGalleryRow(v)) {
                    const childIds = galleryChildren[v.id];
                    const galOn = Boolean(childIds && childIds.length > 0
                      && childIds.every((id) => selectedVideos.includes(id)));
                    const busy = expandingGalleryId === v.id;
                    const count = v.gallery_count ?? childIds?.length ?? 0;
                    const galImg = Boolean(v.thumbnail_url);
                    return (
                      <button
                        type="button"
                        key={v.id}
                        className={`picker-item ${galOn ? 'sel' : ''}`}
                        aria-pressed={galOn}
                        disabled={busy}
                        onClick={() => void onPickGallery(v)}
                      >
                        <span
                          className={`pi-thumb ${galImg ? '' : 'ph'} ${busy ? 'pulse' : ''}`}
                          style={galImg ? { backgroundImage: `url(${v.thumbnail_url})` } : undefined}
                        >
                          <span className="pi-gallery-badge">
                            <Images size={11} strokeWidth={2.5} />
                            {count}
                          </span>
                          {busy && (
                            <span className="pi-busy"><Loader2 size={16} className="spin" /></span>
                          )}
                          {galOn && !busy && (
                            <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                          )}
                        </span>
                        <span className="pi-name" title={v.filename}>{v.filename}</span>
                      </button>
                    );
                  }
                  const on = selectedVideos.includes(v.id);
                  const marked = markedIds.has(v.id);
                  const hasImg = Boolean(v.thumbnail_url);
                  return (
                    <button
                      type="button"
                      key={v.id}
                      className={`picker-item ${on ? 'sel' : ''}`}
                      aria-pressed={on}
                      onClick={() => setSelectedVideos((s) => toggle(s, v.id))}
                    >
                      <span
                        className={`pi-thumb ${hasImg ? '' : 'ph'}`}
                        style={hasImg ? { backgroundImage: `url(${v.thumbnail_url})` } : undefined}
                      >
                        <span
                          role="button"
                          tabIndex={0}
                          className={`pi-mark ${marked ? 'on' : ''}`}
                          aria-label={marked
                            ? t('distribution.publish.pickerUnmark', 'Unmark to publish')
                            : t('distribution.publish.pickerMark', 'Mark to publish')}
                          title={marked
                            ? t('distribution.publish.pickerUnmark', 'Unmark to publish')
                            : t('distribution.publish.pickerMark', 'Mark to publish')}
                          onClick={(e) => { e.stopPropagation(); void onToggleMark(v.id); }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              e.stopPropagation();
                              void onToggleMark(v.id);
                            }
                          }}
                        >
                          <Bookmark size={11} strokeWidth={2.5} />
                        </span>
                        {on && (
                          <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                        )}
                      </span>
                      <span className="pi-name" title={v.filename}>{v.filename}</span>
                    </button>
                  );
                })}
                {pickerResults.length === 0 && (
                  <p className="picker-empty">
                    {videos.length === 0
                      ? (isImages
                        ? t('distribution.publish.noImages', "No uploaded images yet — downloads aren't publishable")
                        : t('distribution.publish.noContent', "No uploads or generated videos yet — downloads aren't publishable"))
                      : toPublishOnly && markedIds.size === 0
                        ? t('distribution.publish.pickerNoMarked', 'No videos marked to publish yet')
                        : t('distribution.publish.pickerNoResults', 'No videos match your search')}
                  </p>
                )}
              </div>
            )}

            {pickerTab === 'generated' && (
              <div className="picker-grid">
                {generatedResults.map((g) => {
                  const resourceId = genResourceIds[g.id];
                  const on = resourceId ? selectedVideos.includes(resourceId) : false;
                  const busy = promotingId === g.id;
                  const name = g.name || t('distribution.publish.generatedUntitled', 'Generated video');
                  return (
                    <button
                      type="button"
                      key={g.id}
                      className={`picker-item ${on ? 'sel' : ''}`}
                      aria-pressed={on}
                      disabled={busy}
                      onClick={() => void onPickGenerated(g)}
                    >
                      <span className={`pi-thumb ph gen ${busy ? 'pulse' : ''}`}>
                        <span className="pi-gen-badge"><Sparkles size={10} /></span>
                        {on && (
                          <span className="pi-check"><Check size={12} strokeWidth={3} /></span>
                        )}
                      </span>
                      <span className="pi-name" title={name}>{name}</span>
                    </button>
                  );
                })}
                {generatedResults.length === 0 && (
                  <p className="picker-empty">
                    {t('distribution.publish.pickerNoGenerated', 'No generated videos yet')}
                  </p>
                )}
              </div>
            )}

            <div className="picker-foot">
              <button type="button" className="btn btn-solid" onClick={() => setPickerOpen(false)}>
                {t('distribution.publish.pickerDone', 'Done')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PublishPage;
