/**
 * My Downloads' right-click menu.
 *
 * This menu is a second, independent implementation from the resource
 * library's (`useContextMenuItems`) — which is how "Send to Agent" shipped
 * to one of them and not the other, hiding the feature from the view that
 * holds most of the user's media. The test exists to make that omission
 * fail loudly rather than silently.
 *
 * It also guards the i18n pass: every label goes through `t()`. The mock
 * below returns the *key* and ignores the default, so a hardcoded English
 * literal (which is what shipped, leaving one Chinese item among eight
 * English ones) cannot satisfy these assertions.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// Swapped per-test: `keyOnly` proves the label came from t(); `withDefaults`
// renders the shipped English copy for assertions about the copy itself.
const keyOnly = (k: string) => k;
const withDefaults = (k: string, def?: string) => def ?? k;
let translate: (k: string, def?: string) => string = keyOnly;

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, def?: string) => translate(k, def) }),
}));

import enLocale from '../../public/locales/en.json';
import zhLocale from '../../public/locales/zh.json';
import { DownloadContextMenu } from './DownloadContextMenu';
import type { Video } from '../../types';

/** Every label the menu renders, in render order. */
const LABEL_KEYS = [
  'resources.viewDetails',
  'resources.openInNewTab',
  'resources.downloadOriginal',
  'resources.downloadAudio',
  'resources.sendToAgent',
  'resources.rename',
  'resources.share',
  'resources.copyLink',
  'resources.delete',
] as const;

/** Reads `resources.<name>` out of a shipped locale bundle. */
function localeLabel(locale: Record<string, unknown>, key: string): string | undefined {
  const [namespace, name] = key.split('.');
  const ns = locale[namespace] as Record<string, string> | undefined;
  return ns?.[name];
}

const video = {
  id: '900001',
  platform_id: 'abc',
  title: 'Clip',
  music_download_path: '/media/900001/audio.mp3',
} as unknown as Video;

function renderMenu(over: Record<string, unknown> = {}) {
  const handlers = {
    onClose: vi.fn(),
    onViewDetails: vi.fn(),
    onOpenNewTab: vi.fn(),
    onDownloadVideo: vi.fn(),
    onDownloadAudio: vi.fn(),
    onSendToAgent: vi.fn(),
    onRename: vi.fn(),
    onShare: vi.fn(),
    onCopyLink: vi.fn(),
    onDelete: vi.fn(),
    onNavigate: vi.fn(),
  };
  render(
    <DownloadContextMenu
      contextMenu={{ x: 10, y: 10, video }}
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

beforeEach(() => {
  translate = keyOnly;
});

describe('DownloadContextMenu', () => {
  it('offers Send to Agent', () => {
    renderMenu();
    expect(screen.getByText('resources.sendToAgent')).toBeInTheDocument();
  });

  it('calls onSendToAgent when it is clicked', () => {
    const handlers = renderMenu();
    fireEvent.click(screen.getByText('resources.sendToAgent'));
    expect(handlers.onSendToAgent).toHaveBeenCalledTimes(1);
  });

  it('renders nothing when the menu is closed', () => {
    renderMenu({ contextMenu: null });
    expect(screen.queryByText('resources.sendToAgent')).toBeNull();
  });

  it('uses no emoji in its labels', () => {
    translate = withDefaults;
    renderMenu();
    expect(document.body.textContent ?? '').not.toMatch(/\p{Extended_Pictographic}/u);
  });
});

describe('DownloadContextMenu labels are translated', () => {
  it.each(LABEL_KEYS)('renders %s through t()', (key) => {
    renderMenu();
    expect(screen.getByText(key)).toBeInTheDocument();
  });

  it('leaks no hardcoded English label when translation is active', () => {
    renderMenu();
    const rendered = document.body.textContent ?? '';
    for (const key of LABEL_KEYS) {
      const english = localeLabel(enLocale, key);
      expect(rendered, `${key} rendered its English copy instead of the key`).not.toContain(
        english,
      );
    }
  });

  it('resolves every label key in both shipped locales', () => {
    for (const [name, locale] of [
      ['en', enLocale],
      ['zh', zhLocale],
    ] as const) {
      for (const key of LABEL_KEYS) {
        expect(localeLabel(locale, key), `${key} missing from ${name}.json`).toBeTruthy();
      }
    }
  });
});
