/**
 * The copy maps are only as good as the keys they name.
 *
 * `t(key, fallback)` never fails: a key that is missing from `zh.json` silently
 * renders the English fallback, which is **exactly the leak this pass set out
 * to close** — and the component test cannot see it, because it mocks
 * react-i18next and reads the fallback by design. So the keys are checked
 * against the shipped locale files here, without React in the way.
 *
 * The repo-wide guard in `components/i18n-rendering.test.tsx` only proves en
 * and zh agree with *each other*; a key that exists in neither passes it. This
 * file is the other half: it proves the keys the component actually asks for
 * exist at all.
 */

import { describe, it, expect } from 'vitest';

import enJson from '../../public/locales/en.json';
import zhJson from '../../public/locales/zh.json';
import { SERVER_DETAIL_COPY, SMS_SUBMIT_COPY } from './SessionLoginModal';

const lookup = (bundle: unknown, key: string): unknown =>
  key.split('.').reduce<unknown>(
    (node, part) =>
      node && typeof node === 'object'
        ? (node as Record<string, unknown>)[part]
        : undefined,
    bundle,
  );

const MAPS = {
  SERVER_DETAIL_COPY,
  SMS_SUBMIT_COPY,
} as const;

describe('session login copy maps', () => {
  for (const [name, map] of Object.entries(MAPS)) {
    const entries = Object.entries(map);

    it(`${name} names keys that exist in en and zh`, () => {
      for (const [status, [key]] of entries) {
        expect(typeof lookup(enJson, key), `${name}.${status} → ${key} missing from en.json`)
          .toBe('string');
        expect(typeof lookup(zhJson, key), `${name}.${status} → ${key} missing from zh.json`)
          .toBe('string');
      }
    });

    it(`${name} ships a real translation, not the English copied over`, () => {
      // A zh value byte-identical to en is a key someone added to keep the
      // symmetry guard quiet. It renders as English under a Chinese UI, which
      // is the same user-visible bug as having no key at all.
      for (const [status, [key]] of entries) {
        const en = lookup(enJson, key);
        const zh = lookup(zhJson, key);
        expect(zh, `${name}.${status} → ${key} is untranslated`).not.toBe(en);
      }
    });

    it(`${name} covers every SessionLoginStatus`, () => {
      // Compile time already enforces this (the maps are `Record<...>`), but a
      // future `Partial` or an `as` cast would slip past the compiler while
      // reintroducing the echo path. Cheap to pin.
      expect(new Set(entries.map(([status]) => status))).toEqual(
        new Set([
          'waiting_scan',
          'scanned',
          'qrcode_expired',
          // The platform's own identity check. Its copy has to exist in both
          // locales like every other status — and it is the one state whose
          // wording is load-bearing rather than cosmetic: it is what stops the
          // UI promising a code that has not been asked for yet.
          'identity_challenge',
          // Platforms with no QR code at all, waiting for the account's phone
          // number. Same load-bearing role as the state above, one step
          // earlier: it is what stops the UI asking for a code before anyone
          // has told the platform where to send one.
          'phone_required',
          'sms_required',
          'success',
          'timeout',
          'failed',
          'proxy_failed',
        ]),
      );
    });
  }
});
