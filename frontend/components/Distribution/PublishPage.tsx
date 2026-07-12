import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { Check, Send } from 'lucide-react';
import { PageHeader } from '../AILibrary/PageHeader';
import { useToast } from '../Toast';
import {
  createPublishTask, listAccounts, listLibraryVideos,
} from '../../services/distributionService';
import { SocialAccount, LibraryVideo } from '../../types';

type Visibility = 'public' | 'friends' | 'private';
type Mode = 'broadcast' | 'one_to_one';
type Channel = 'official' | 'h5';

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
  const [submitting, setSubmitting] = useState(false);

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

  const toggle = (list: string[], id: string): string[] =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  const canPublish = useMemo(
    () => selectedVideos.length > 0 && selectedAccounts.length > 0 && title.trim().length > 0,
    [selectedVideos, selectedAccounts, title],
  );

  const onPublish = async () => {
    if (!canPublish || submitting) return;
    setSubmitting(true);
    try {
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

  const VIS: Visibility[] = ['public', 'friends', 'private'];

  return (
    <div className="pt-6">
      <PageHeader
        title={t('distribution.publish.title', 'Publish')}
        subtitle={t('distribution.publish.subtitle', 'Send a video from your Library to connected accounts')}
      />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_320px]">
        {/* Left column */}
        <div className="flex flex-col gap-5">
          <section>
            <h2 className="mb-2 text-[13px] font-semibold text-ink-200">
              {t('distribution.publish.content', 'Content')}
            </h2>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(140px,1fr))] gap-2">
              {videos.map((v) => {
                const on = selectedVideos.includes(v.id);
                return (
                  <button key={v.id} onClick={() => setSelectedVideos((s) => toggle(s, v.id))}
                    className={`relative flex aspect-video items-end rounded-lg border p-2 text-left text-[11px] ${
                      on ? 'border-indigo-500 bg-indigo-500/10 text-ink-100'
                         : 'border-ink-800 bg-ink-900/60 text-ink-400 hover:border-ink-700'}`}>
                    {on && <Check size={14} className="absolute right-1.5 top-1.5 text-indigo-300" />}
                    <span className="truncate">{v.filename}</span>
                  </button>
                );
              })}
              {videos.length === 0 && (
                <p className="text-[12px] text-ink-600">
                  {t('distribution.publish.noContent', 'No video resources in your Library yet')}
                </p>
              )}
            </div>
          </section>

          <section>
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={500}
              placeholder={t('distribution.publish.titlePlaceholder', 'Add a title')}
              className="w-full rounded-lg border border-ink-800 bg-ink-900/60 px-3 py-2 text-[14px] text-ink-100 placeholder:text-ink-600" />
            <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
              placeholder={t('distribution.publish.descPlaceholder', 'Add a description')}
              className="mt-2 w-full resize-none rounded-lg border border-ink-800 bg-ink-900/60 px-3 py-2 text-[13px] text-ink-200 placeholder:text-ink-600" />
          </section>

          <section className="flex flex-wrap gap-4 text-[12px] text-ink-300">
            <label className="flex items-center gap-2">
              <span className="text-ink-500">{t('distribution.publish.visibility', 'Visibility')}</span>
              <select value={visibility} onChange={(e) => setVisibility(e.target.value as Visibility)}
                className="rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-ink-200">
                {VIS.map((v) => (
                  <option key={v} value={v}>{t(`distribution.publish.vis_${v}`, v)}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={aiContent} onChange={(e) => setAiContent(e.target.checked)} />
              {t('distribution.publish.aiContent', 'AI-generated content')}
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={allowDownload} onChange={(e) => setAllowDownload(e.target.checked)} />
              {t('distribution.publish.allowDownload', 'Allow download')}
            </label>
          </section>

          <section className="flex gap-2 text-[12px]">
            {(['broadcast', 'one_to_one'] as Mode[]).map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`rounded-md border px-3 py-1.5 ${
                  mode === m ? 'border-indigo-500 bg-indigo-500/10 text-indigo-300'
                             : 'border-ink-800 text-ink-400 hover:border-ink-700'}`}>
                {t(`distribution.publish.mode_${m}`, m === 'broadcast' ? 'Broadcast' : 'One-to-one')}
              </button>
            ))}
          </section>
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4 rounded-2xl border border-ink-800 bg-ink-900/40 p-4">
          <h2 className="text-[13px] font-semibold text-ink-200">
            {t('distribution.publish.accounts', 'Target accounts')}
          </h2>
          <div className="flex flex-col gap-1.5">
            {accounts.map((a) => {
              const expired = a.status === 'expired';
              const on = selectedAccounts.includes(a.id);
              return (
                <button key={a.id} disabled={expired}
                  onClick={() => setSelectedAccounts((s) => toggle(s, a.id))}
                  className={`flex items-center justify-between rounded-lg border px-3 py-2 text-left text-[12.5px] ${
                    expired ? 'cursor-not-allowed border-ink-800 text-ink-600'
                      : on ? 'border-indigo-500 bg-indigo-500/10 text-ink-100'
                           : 'border-ink-800 text-ink-300 hover:border-ink-700'}`}>
                  <span className="truncate">{a.username}</span>
                  {expired
                    ? <span className="text-[10px] text-amber-400">{t('distribution.expired', 'Authorization expired')}</span>
                    : on && <Check size={14} className="text-indigo-300" />}
                </button>
              );
            })}
            {accounts.length === 0 && (
              <p className="text-[12px] text-ink-600">
                {t('distribution.publish.noAccounts', 'Connect an account first')}
              </p>
            )}
          </div>

          <label className="flex items-center justify-between text-[12px] text-ink-300">
            <span className="text-ink-500">{t('distribution.publish.channel', 'Channel')}</span>
            <select value={channel} onChange={(e) => setChannel(e.target.value as Channel)}
              className="rounded-md border border-ink-800 bg-ink-900 px-2 py-1 text-ink-200">
              <option value="h5">{t('distribution.publish.channel_h5', 'H5 share (finish on phone)')}</option>
              <option value="official">{t('distribution.publish.channel_official', 'Official API')}</option>
            </select>
          </label>

          <div className="mt-2 border-t border-ink-800 pt-3 text-[11px] text-ink-500">
            {t('distribution.publish.summary', '{{v}} video(s) → {{a}} account(s)', {
              v: selectedVideos.length, a: selectedAccounts.length,
            })}
            {channel === 'h5' && (
              <p className="mt-1 text-amber-400/80">
                {t('distribution.publish.h5Hint', 'H5: each account is finished on the Douyin app')}
              </p>
            )}
          </div>

          <button onClick={onPublish} disabled={!canPublish || submitting}
            className="btn-tint-indigo flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-[13px] font-medium disabled:cursor-not-allowed disabled:opacity-40">
            <Send size={15} /> {t('distribution.publish.publishNow', 'Publish now')}
          </button>
        </div>
      </div>
    </div>
  );
};

export default PublishPage;
