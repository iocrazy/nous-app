/**
 * Pick the image source for a media card frame.
 *
 * Remote slide URLs (`image_download_urls`) are signed CDN links that expire
 * — douyin's in days. The locally downloaded cover (`cover_download_path`,
 * served via getCoverUrl) never does. So the default frame (index 0) prefers
 * the local cover and only falls back to the remote first slide; flipped
 * album frames have no local counterpart (only the cover is downloaded) and
 * keep using their remote URL.
 */
export function pickCoverFrame(
  localCover: string | undefined,
  remoteImages: string[],
  index: number = 0,
): string | undefined {
  if (index === 0) return localCover || remoteImages[0] || undefined;
  return remoteImages[index];
}
