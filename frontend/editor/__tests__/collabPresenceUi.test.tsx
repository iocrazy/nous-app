import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PresenceAvatars } from '../collab/PresenceAvatars';
import { ScenePresenceBadge } from '../collab/ScenePresenceBadge';
import type { PresenceUser } from '../collab/useScriptPresence';

// i18n: echo the key (+ count when present) so assertions are language-stable.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, opts?: Record<string, unknown>) =>
      opts && opts.count != null ? `${k}:${opts.count}` : k,
  }),
}));

const user = (over: Partial<PresenceUser>): PresenceUser => ({
  user_id: 'u1',
  name: 'Alice',
  focused_scene_id: null,
  mode: 'viewing',
  ...over,
});

afterEach(cleanup);

describe('PresenceAvatars', () => {
  it('renders nothing when no one else is present', () => {
    const { container } = render(<PresenceAvatars users={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders one initial bubble per user', () => {
    render(
      <PresenceAvatars
        users={[user({ user_id: 'a', name: 'Alice' }), user({ user_id: 'b', name: 'bob' })]}
      />,
    );
    const stack = screen.getByTestId('presence-avatars');
    expect(stack.textContent).toContain('A');
    expect(stack.textContent).toContain('B'); // initial is uppercased
  });

  it('collapses the tail into +N above the max', () => {
    const users = ['a', 'b', 'c', 'd', 'e', 'f'].map((id) =>
      user({ user_id: id, name: id }),
    );
    render(<PresenceAvatars users={users} max={4} />);
    // 4 shown + 1 overflow chip.
    expect(screen.getByTestId('presence-avatars').textContent).toContain('+2');
  });

  it('marks an editing user with the editing modifier', () => {
    const { container } = render(
      <PresenceAvatars users={[user({ mode: 'editing', name: 'Zoe' })]} />,
    );
    expect(container.querySelector('.mh-presence-avatar.editing')).not.toBeNull();
  });
});

describe('ScenePresenceBadge', () => {
  it('renders nothing when no one is focused here', () => {
    const { container } = render(<ScenePresenceBadge users={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows a viewer count when everyone is only viewing', () => {
    render(
      <ScenePresenceBadge
        users={[user({ user_id: 'a' }), user({ user_id: 'b' })]}
      />,
    );
    const badge = screen.getByTestId('scene-presence-badge');
    expect(badge.textContent).toBe('editor.collab.viewersCount:2');
    expect(badge.className).not.toContain('editing');
  });

  it('shows the editing badge (precedence) when anyone is editing', () => {
    render(
      <ScenePresenceBadge
        users={[user({ user_id: 'a', mode: 'viewing' }), user({ user_id: 'b', mode: 'editing' })]}
      />,
    );
    const badge = screen.getByTestId('scene-presence-badge');
    expect(badge.textContent).toBe('editor.collab.editingBadge');
    expect(badge.className).toContain('editing');
  });
});
