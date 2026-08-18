/**
 * The self-declaration field uses the app's own dropdown, not a native
 * `<select>`.
 *
 * WHAT BROKE: this was the ONE native `<select>` left on the publish page, so
 * it painted with the operating system's widget — its own highlight colour and
 * font — while every other control on the page used the shared menu.
 *
 * The swap is not cosmetic-only: Douyin's declarations are long strings
 * ("Fictional dramatization, entertainment only"), and the shared menu used to
 * pin its panel to the trigger's width, which would have ellipsised them. That
 * is why this change and the `UiSelect` panel-sizing change ship together —
 * see `components/ui/UiSelect.menuWidth.test.tsx`.
 *
 * What makes this test fall over on a revert: a native `<select>` exposes the
 * combobox role on the `<select>` element itself and has no button trigger and
 * no portalled listbox, so `getByRole('button', { name: 'Self declaration' })`
 * finds nothing.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance, type i18n as I18n } from 'i18next';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import enJson from '../../public/locales/en.json';

const { listAccounts } = vi.hoisted(() => ({ listAccounts: vi.fn() }));

const DOUYIN_ACCOUNT = {
  id: '10', scope_type: 'user', scope_id: 'u1', platform: 'douyin',
  platform_user_id: 'op1', username: 'HEYGO', avatar_url: null,
  auth_type: 'session', token_expires_at: null, status: 'active',
  created_at: '2026-08-11T00:00:00Z',
};

vi.mock('../../services/distributionService', async (orig) => {
  const actual = await orig<typeof import('../../services/distributionService')>();
  return {
    ...actual,
    listAccounts,
    getPlatformCapabilities: vi.fn().mockResolvedValue({}),
    createPublishTask: vi.fn(),
    listLibraryMedia: vi.fn().mockResolvedValue([]),
    listGeneratedVideos: vi.fn().mockResolvedValue([]),
    promoteGeneratedVideo: vi.fn(),
    extractCoverFrames: vi.fn(),
    selectCoverFrame: vi.fn(),
  };
});

vi.mock('../../services/unifiedTagService', () => ({
  fetchAllTags: vi.fn().mockResolvedValue([]),
  createTag: vi.fn().mockResolvedValue({ id: 'tag-1', name: 'To Publish' }),
  addResourceTag: vi.fn().mockResolvedValue(undefined),
  removeResourceTag: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('../../services/resourceService', () => ({
  uploadResource: vi.fn(),
  getGalleryItems: vi.fn().mockResolvedValue([]),
  getResourceCoverUrl: (id: string) => `/cover/${id}`,
  getResourceFileUrl: (id: string) => `/file/${id}`,
  GALLERY_MIME: 'application/x-mediahub-gallery',
}));

vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<typeof import('react-router-dom')>()),
  useNavigate: () => vi.fn(),
}));

vi.mock('../Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));

vi.mock('../../supabaseClient', () => {
  const channelObj = { on: () => channelObj, subscribe: () => channelObj };
  return {
    getSupabaseAccessToken: () => Promise.resolve('jwt'),
    getSupabaseClient: () => ({
      auth: {
        getSession: () => Promise.resolve({ data: { session: { access_token: 'jwt' } } }),
      },
      channel: () => channelObj,
      removeChannel: vi.fn(),
      from: () => ({
        select: () => ({
          eq: () => ({ maybeSingle: () => Promise.resolve({ data: null, error: null }) }),
        }),
      }),
    }),
  };
});

vi.mock('../../contexts/TeamContext', () => ({
  useTeamContext: () => ({ personalTeamId: 'pt1' }),
}));

import PublishPage from './PublishPage';

const makeI18n = (): I18n => {
  const inst = createInstance();
  void inst.use(initReactI18next).init({
    lng: 'en',
    fallbackLng: 'en',
    resources: { en: { translation: enJson } },
    interpolation: { escapeValue: false },
    react: { useSuspense: false },
  });
  return inst;
};

/** Every declaration Douyin accepts, as the user sees them. */
const DECLARATIONS = [
  'AI-generated content',
  'Personal opinion or insight',
  'Reposted information',
  'Contains marketing or promotion',
  'Fictional dramatization, entertainment only',
  'No declaration needed',
];

const renderPage = async () => {
  listAccounts.mockResolvedValue([DOUYIN_ACCOUNT]);
  const utils = render(
    <I18nextProvider i18n={makeI18n()}>
      <MemoryRouter><PublishPage /></MemoryRouter>
    </I18nextProvider>,
  );
  await waitFor(() => expect(screen.getByText('HEYGO')).toBeInTheDocument());
  return utils;
};

const declarationTrigger = () => screen.getByRole('button', { name: 'Self declaration' });

describe('publish — self declaration uses the shared dropdown', () => {
  it('renders a button trigger with a listbox popup, not a native select', async () => {
    await renderPage();

    const trigger = declarationTrigger();
    // A native <select> would be tagName SELECT and carry neither of these.
    expect(trigger.tagName).toBe('BUTTON');
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox');
    expect(trigger.getAttribute('aria-expanded')).toBe('false');

    fireEvent.click(trigger);
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('listbox', { name: 'Self declaration' })).toBeInTheDocument();
  });

  it('offers every declaration plus the unset option, with labels in full', async () => {
    await renderPage();
    fireEvent.click(declarationTrigger());
    const menu = screen.getByRole('listbox', { name: 'Self declaration' });

    // Leaving it unset is a real choice (keep the platform default), so it is
    // an option rather than an empty trigger.
    expect(within(menu).getByRole('option', { name: 'Not set' })).toBeInTheDocument();
    for (const label of DECLARATIONS) {
      expect(within(menu).getByRole('option', { name: label })).toBeInTheDocument();
    }
    expect(within(menu).getAllByRole('option')).toHaveLength(DECLARATIONS.length + 1);
  });

  it('selects a declaration by clicking its option', async () => {
    await renderPage();
    fireEvent.click(declarationTrigger());
    fireEvent.click(screen.getByRole('option', { name: 'Reposted information' }));

    // The trigger now reads back the chosen declaration, and the menu closed.
    await waitFor(() =>
      expect(declarationTrigger().textContent).toContain('Reposted information'));
    // The panel unmounts after its close transition, not synchronously.
    await waitFor(() =>
      expect(screen.queryByRole('listbox', { name: 'Self declaration' })).toBeNull());
  });

  it('keeps the mirror <select> hidden from assistive tech', async () => {
    // UiSelect keeps a real <select> behind the trigger for form semantics.
    // It must stay aria-hidden, or the page would expose the field twice —
    // once as the styled control and once as the OS widget we just removed.
    await renderPage();
    const mirror = declarationTrigger().parentElement?.querySelector('select');
    expect(mirror).not.toBeNull();
    expect(mirror?.getAttribute('aria-hidden')).toBe('true');
  });
});
