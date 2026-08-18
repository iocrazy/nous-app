/**
 * My Downloads → Send to Agent: the identity translation.
 *
 * The view holds `parsed_media` rows (1242 of them for this user); every AI
 * trigger and the chat chip are keyed by `resources.id`. Getting that
 * mapping wrong is not a cosmetic bug — passing a `parsed_media` id to the
 * transcribe endpoint addresses a different row entirely.
 */
import { describe, it, expect } from 'vitest';
import { buildDownloadAgentPayload } from './downloadAgentPayload';
import type { ResourceData } from './useDownloadsData';

// Real `resources` row shape as PostgREST returns it for this page (see
// useResourceDataMap's select). Note `file_type` is deliberately absent:
// downloads store aweme-type numerals there, so nothing may read it.
const resourceData = (over: Partial<ResourceData> = {}): ResourceData => ({
  id: '339710259795355',
  filename: 'clip.mp4',
  mime_type: 'video/mp4',
  notes: null,
  rating: 0,
  transcript_status: 'none',
  summary_status: 'none',
  ...over,
});

const video = { id: '900001', title: 'Some Clip', platform_id: 'abc123' };

describe('buildDownloadAgentPayload', () => {
  it('sends the RESOURCE id, never the parsed_media id', () => {
    const out = buildDownloadAgentPayload(video, resourceData());
    expect(out.ok).toBe(true);
    if (!out.ok) return;
    expect(out.resource.id).toBe('339710259795355');
    expect(out.resource.id).not.toBe('900001');
  });

  it('keeps the parsed_media id as the cover signal', () => {
    // Downloads keep their cover on parsed_media, so this FK is what makes
    // the chip paint a thumbnail instead of a generic icon.
    const out = buildDownloadAgentPayload(video, resourceData());
    if (!out.ok) throw new Error('expected ok');
    expect(out.resource.media_id).toBe('900001');
  });

  it('carries the processing status through', () => {
    // Dropping these re-triggers a PAID transcription on an already
    // transcribed video: the endpoint dedups in-flight work only.
    const out = buildDownloadAgentPayload(
      video,
      resourceData({ transcript_status: 'completed', summary_status: 'completed' }),
    );
    if (!out.ok) throw new Error('expected ok');
    expect(out.resource.transcript_status).toBe('completed');
    expect(out.resource.summary_status).toBe('completed');
  });

  it('carries the mime so the kind can be derived', () => {
    const out = buildDownloadAgentPayload(video, resourceData({ mime_type: 'audio/mp4' }));
    if (!out.ok) throw new Error('expected ok');
    expect(out.resource.mime_type).toBe('audio/mp4');
  });

  it('labels the chip with the resource filename', () => {
    const out = buildDownloadAgentPayload(video, resourceData());
    if (!out.ok) throw new Error('expected ok');
    expect(out.resource.filename).toBe('clip.mp4');
  });

  it('falls back to the card title when the resource has no filename', () => {
    const out = buildDownloadAgentPayload(video, resourceData({ filename: null }));
    if (!out.ok) throw new Error('expected ok');
    expect(out.resource.filename).toBe('Some Clip');
  });

  it('reports a typed failure when no resource is linked', () => {
    // The alternative — returning something half-built — is how a Send to
    // Agent turns into a silent no-op.
    expect(buildDownloadAgentPayload(video, undefined)).toEqual({
      ok: false,
      reason: 'no_resource',
    });
  });

  it('reports a typed failure when the map row has no id', () => {
    const out = buildDownloadAgentPayload(video, { ...resourceData(), id: '' });
    expect(out).toEqual({ ok: false, reason: 'no_resource' });
  });
});
