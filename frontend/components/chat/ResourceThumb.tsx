import React, { useState } from 'react';
import { FileText, Image, Video, Music, FileType2 } from 'lucide-react';
import { resolveResourceThumbnailSrc } from './resourceStatus';

const ICON_BY_KIND: Record<string, React.ComponentType<{ size?: number }>> = {
  video: Video, image: Image, audio: Music, doc: FileText, pdf: FileType2,
};

interface Props {
  /** Relative cover path from the API, or null/'' when there is none. */
  thumbnailUrl: string | null | undefined;
  kind: string;
  imgClassName: string;
  iconClassName: string;
  iconSize: number;
  imgTestId: string;
  iconTestId: string;
}

/**
 * Cover image with a real icon fallback, shared by the picker row and the
 * inline chip.
 *
 * The fallback has to be driven by `onError`, not just by a null URL:
 * `/resources/search` returns a cover URL whenever the row *could plausibly*
 * have one (an explicit thumbnail, an explicit cover, or a parsed_media
 * backing row), but `serve_resource_cover` only reaches its inline-SVG
 * placeholder on the independent-resource branch. A media_id backed
 * resource whose cover sits in S3 gets a flat 404 instead — which is the
 * common case in the picker, since those are downloads. So "we handed out a
 * URL" and "an image comes back" are genuinely different questions, and the
 * null-URL branch alone can never answer the second one.
 *
 * It lives in its own component because the picker paints rows inside a
 * `.map` — per-row failure state has nowhere else to go.
 */
export function ResourceThumb({
  thumbnailUrl,
  kind,
  imgClassName,
  iconClassName,
  iconSize,
  imgTestId,
  iconTestId,
}: Props): React.ReactElement {
  const [failed, setFailed] = useState(false);
  const src = failed ? null : resolveResourceThumbnailSrc(thumbnailUrl);
  const Icon = ICON_BY_KIND[kind] ?? FileText;

  if (!src) {
    return (
      <span data-testid={iconTestId} className={iconClassName}>
        <Icon size={iconSize} />
      </span>
    );
  }
  return (
    <img
      data-testid={imgTestId}
      src={src}
      alt=""
      loading="lazy"
      className={imgClassName}
      onError={() => setFailed(true)}
    />
  );
}
