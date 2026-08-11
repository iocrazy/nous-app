import React from 'react';

/**
 * Shared platform presentation helpers for the Distribution pages.
 * (PublishPage / RecordsPage still carry local copies from their v4 port —
 * collapsing them onto this module is a separate refactor PR.)
 */

export const PLATFORM_LABEL: Record<string, string> = {
  douyin: 'Douyin', kuaishou: 'Kuaishou', xiaohongshu: 'Xiaohongshu',
  bilibili: 'Bilibili',
};

export const PLATFORM_BADGE: Record<string, { bg: string; icon: React.ReactNode }> = {
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
  bilibili: {
    bg: '#00A1D6',
    icon: (
      <svg viewBox="0 0 24 24" fill="#fff">
        <path d="M7.2 3.6 9 5.4h6l1.8-1.8 1.2 1.2-.9.9h1.2A2.7 2.7 0 0 1 21 8.4v9a2.7 2.7 0 0 1-2.7 2.7H5.7A2.7 2.7 0 0 1 3 17.4v-9a2.7 2.7 0 0 1 2.7-2.7h1.2l-.9-.9ZM5.7 8.1a.3.3 0 0 0-.3.3v9c0 .17.13.3.3.3h12.6a.3.3 0 0 0 .3-.3v-9a.3.3 0 0 0-.3-.3Zm2.4 2.4v2.4H9.9v-2.4Zm6 0v2.4h1.8v-2.4Z" />
      </svg>
    ),
  },
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

export const gradientFor = (id: string): string => {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return AVA_GRADIENTS[h % AVA_GRADIENTS.length];
};

export interface AccountAvatarProps {
  /**
   * Placeholder background. Passed in rather than derived here because the
   * three Distribution pages still carry their own `gradientFor` copies from
   * the v4 port (different palette lengths → different pick for the same id);
   * collapsing them is the separate refactor this module's header mentions.
   */
  gradient: string;
  username: string;
  /** `social_accounts.avatar_url` — null whenever the platform never gave one. */
  avatarUrl?: string | null;
  /** Extra classes on the `.ava` wrapper (size variants live in the CSS). */
  className?: string;
  /** Platform badge etc. — rendered above the image. */
  children?: React.ReactNode;
}

/**
 * Account avatar with the initials-on-gradient tile as its floor.
 *
 * The tile is never removed: the real avatar is layered on top of it, so a
 * null `avatar_url` and a CDN that stops serving the image both land on the
 * same readable placeholder instead of a hole. `onError` is what makes the
 * second case work — an <img> whose request fails renders as a broken-image
 * glyph, not as nothing.
 *
 * `referrerPolicy="no-referrer"` is precautionary: the douyinpic CDN serves
 * these unauthenticated today, but hotlink protection is the kind of thing a
 * platform turns on without notice, and sending no Referer is what the
 * platforms' own web clients do.
 */
export const AccountAvatar: React.FC<AccountAvatarProps> = ({
  gradient, username, avatarUrl, className, children,
}) => {
  const [broken, setBroken] = React.useState(false);
  // A row can be re-keyed onto a different account (list re-sort, realtime
  // update); without this, one dead URL would suppress every later avatar
  // rendered by the same component instance.
  React.useEffect(() => { setBroken(false); }, [avatarUrl]);

  const showImage = Boolean(avatarUrl) && !broken;
  return (
    <span className={className ? `ava ${className}` : 'ava'} style={{ background: gradient }}>
      {username.slice(0, 2).toUpperCase()}
      {showImage && (
        <img
          className="ava-img"
          src={avatarUrl as string}
          alt={username}
          referrerPolicy="no-referrer"
          loading="lazy"
          onError={() => setBroken(true)}
        />
      )}
      {children}
    </span>
  );
};
