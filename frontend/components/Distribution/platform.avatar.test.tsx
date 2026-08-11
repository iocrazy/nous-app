import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AccountAvatar } from './platform';

// D3: account avatars were reaching the frontend (social_accounts.avatar_url,
// joined onto every account-shaped payload) and being thrown away — every
// Distribution page drew the deterministic gradient tile instead. These cover
// the three states the tile has to survive.

const GRADIENT = 'linear-gradient(135deg,#0ea5e9,#6366f1)';
const URL = 'https://p3-pc.douyinpic.com/aweme/100x100/avatar.jpeg';

describe('AccountAvatar', () => {
  it('renders the real avatar when the account has one', () => {
    render(<AccountAvatar gradient={GRADIENT} username="HEYGO" avatarUrl={URL} />);

    const img = screen.getByRole('img', { name: 'HEYGO' });
    expect(img).toHaveAttribute('src', URL);
    // Precaution against the CDN turning on hotlink protection later.
    expect(img).toHaveAttribute('referrerpolicy', 'no-referrer');
  });

  it('falls back to the gradient tile when avatar_url is null', () => {
    const { container } = render(
      <AccountAvatar gradient={GRADIENT} username="HEYGO" avatarUrl={null} />,
    );

    expect(container.querySelector('img')).toBeNull();
    const tile = container.querySelector('.ava') as HTMLElement;
    expect(tile).toBeTruthy();
    expect(tile.textContent).toContain('HE');
    expect(tile.style.background).toContain('linear-gradient');
  });

  it('falls back to the gradient tile when the image fails to load', () => {
    const { container } = render(
      <AccountAvatar gradient={GRADIENT} username="HEYGO" avatarUrl={URL} />,
    );

    const img = screen.getByRole('img', { name: 'HEYGO' });
    // A broken <img> renders the browser's broken-image glyph, not nothing —
    // so the fallback only happens if onError actually drops the element.
    fireEvent.error(img);

    expect(container.querySelector('img')).toBeNull();
    const tile = container.querySelector('.ava') as HTMLElement;
    expect(tile.textContent).toContain('HE');
    expect(tile.style.background).toContain('linear-gradient');
  });

  it('retries when the row is re-pointed at a different account', () => {
    const { rerender, container } = render(
      <AccountAvatar gradient={GRADIENT} username="HEYGO" avatarUrl={URL} />,
    );
    fireEvent.error(screen.getByRole('img', { name: 'HEYGO' }));
    expect(container.querySelector('img')).toBeNull();

    // Same component instance, different account (list re-sort / realtime
    // update). One dead URL must not suppress every later avatar.
    rerender(
      <AccountAvatar gradient={GRADIENT} username="Studio" avatarUrl={`${URL}?v=2`} />,
    );
    expect(screen.getByRole('img', { name: 'Studio' })).toHaveAttribute('src', `${URL}?v=2`);
  });

  it('keeps the platform badge above the image', () => {
    const { container } = render(
      <AccountAvatar gradient={GRADIENT} username="HEYGO" avatarUrl={URL}>
        <span className="pbadge" data-testid="badge" />
      </AccountAvatar>,
    );

    expect(container.querySelector('.ava .pbadge')).toBeTruthy();
    expect(screen.getByRole('img', { name: 'HEYGO' })).toBeTruthy();
  });
});
