import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import {
  AlertCircle, AlertTriangle, ArrowLeftRight, Calendar, Check, Folder,
  ListOrdered, MapPin, Plus, Radio, Search, Send, Sparkles, TrendingUp, X,
} from 'lucide-react';
import {
  createPublishTask, listAccounts, listLibraryVideos,
} from '../../services/distributionService';
import { SocialAccount, LibraryVideo } from '../../types';
import { useToast } from '../Toast';
import './distribution-v4.css';

type Visibility = 'public' | 'friends' | 'private';
type Mode = 'broadcast' | 'one_to_one';
type Channel = 'official' | 'h5';
type Orientation = 'vertical' | 'horizontal';

const VIS: Visibility[] = ['public', 'friends', 'private'];
const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
};

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
  const { teamId } = useParams();

  const [videos, setVideos] = useState<LibraryVideo[]>([]);
  const [accounts, setAccounts] = useState<SocialAccount[]>([]);
  const [selectedVideos, setSelectedVideos] = useState<string[]>([]);
  const [selectedAccounts, setSelectedAccounts] = useState<string[]>([]);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
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

  const load = useCallback(async () => {
    try {
      const [v, a] = await Promise.all([listLibraryVideos(teamId ?? ''), listAccounts()]);
      setVideos(v);
      setAccounts(a);
    } catch (err) {
      console.error('distribution: publish page load failed', err);
      addToast(t('distribution.publish.loadFailed', 'Failed to load publish data'), 'error');
    }
  }, [addToast, t, teamId]);

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
    return q ? videos.filter((v) => v.filename.toLowerCase().includes(q)) : videos;
  }, [videos, pickerQuery]);

  const canPublish = useMemo(
    () => selectedVideos.length > 0 && selectedAccounts.length > 0 && title.trim().length > 0,
    [selectedVideos, selectedAccounts, title],
  );

  const postsBroadcast = selectedVideos.length * selectedAccounts.length;
  const postsOneToOne = selectedVideos.length > 0 ? selectedAccounts.length : 0;
  const totalPosts = mode === 'broadcast' ? postsBroadcast : postsOneToOne;

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
        content_type: 'video',
        resource_ids: selectedVideos,
        title: title.trim(),
        description: description.trim() || undefined,
        visibility,
        ai_content: aiContent,
        allow_download: allowDownload,
        distribution_mode: mode,
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
      <div className="page-head">
        <div>
          <h2>{t('distribution.publish.title', 'Publish')}</h2>
          <p>{t('distribution.publish.subtitle', 'Send library media to your connected accounts.')}</p>
        </div>
      </div>

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
              <span className="aux">{t('distribution.publish.videoSelectedCount', 'Video · {{n}} selected', { n: selectedVideos.length })}</span>
            </h4>
            <div className="seg">
              <button type="button" className="on">{t('distribution.publish.fromLibrary', 'From Library')}</button>
              <button type="button" disabled title={t('distribution.comingInD3', 'Coming in D3')}>{t('distribution.publish.upload', 'Upload')}</button>
            </div>
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
                    <span className="play">
                      <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                    </span>
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
                <div className="cover-slot v">
                  <Sparkles />
                  {t('distribution.publish.vertical34', 'Vertical 3:4')}
                </div>
                <div className="cover-slot h">
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
              <span className="chip chip-mute">{t('distribution.publish.topicChip', '# Topic')}</span>
              <span className="chip chip-mute">{t('distribution.publish.mentionChip', '@ Mention')}</span>
            </div>
            <div className="topics" style={{ marginTop: 7 }}>
              <span className="trending-label">{t('distribution.publish.trending', 'Trending')}</span>
              <span className="chip chip-mute">#goldenhour</span>
              <span className="chip chip-mute">#cityscape</span>
              <span className="chip chip-mute">#4k</span>
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
                <span>{t('distribution.publish.aiContentDesc', 'Adds the disclosure label on platforms that require it')}</span>
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
              <div className="scene" />
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
              <button type="button" className={channel === 'official' ? 'on' : ''} onClick={() => setChannel('official')}>{t('distribution.publish.channel_official', 'Official API')}</button>
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
            <div className="line"><span>{t('distribution.publish.videosLabel', 'Videos')}</span><b>{selectedVideos.length}</b></div>
            <div className="line"><span>{t('distribution.publish.accountsLabel', 'Accounts')}</span><b>{selectedAccounts.length}</b></div>
            <div className="line"><span>{t('distribution.publish.modeLabel', 'Mode')}</span><b>{mode === 'broadcast' ? t('distribution.publish.mode_broadcast', 'Broadcast') : t('distribution.publish.mode_one_to_one', 'One-to-one')}</b></div>
            <div className="line total"><span>{t('distribution.publish.postsToCreate', 'Posts to create')}</span><b>{totalPosts}</b></div>

            <div className="check warn">
              <AlertTriangle />
              {t('distribution.publish.coverNotSetWarning', 'Cover not set — a video-frame cover will be used automatically.')}
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

            <div className="picker-grid">
              {pickerResults.map((v) => {
                const on = selectedVideos.includes(v.id);
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
                    ? t('distribution.publish.noContent', 'No video resources in your Library yet')
                    : t('distribution.publish.pickerNoResults', 'No videos match your search')}
                </p>
              )}
            </div>

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
