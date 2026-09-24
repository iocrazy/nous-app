/**
 * Gallery MIME rename, step 3 (cleanup): only the new spelling is a gallery.
 * Migration 488 rewrote the last legacy row, and no deployed backend writes the
 * legacy value any more, so it is rejected like any other non-gallery mime.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import * as galleryMimeModule from './galleryMime';
import { GALLERY_MIME, GALLERY_MIMES, isGalleryMime } from './galleryMime';

const NEW = 'application/x-nous-gallery';
// Assembled so this file does not trip its own "no legacy literal" scan below.
const OLD = ['application/x', 'mediahub', 'gallery'].join('-');

describe('galleryMime', () => {
  it('spells the new value', () => {
    expect(GALLERY_MIME).toBe(NEW);
  });

  it('accepts only the new value', () => {
    expect(GALLERY_MIMES).toEqual([NEW]);
    expect(isGalleryMime(NEW)).toBe(true);
  });

  it('no longer exports a legacy spelling', () => {
    expect(Object.keys(galleryMimeModule).sort()).toEqual(
      ['GALLERY_MIME', 'GALLERY_MIMES', 'isGalleryMime'].sort(),
    );
  });

  it('rejects everything else, including the retired legacy spelling', () => {
    for (const m of [OLD, null, undefined, '', 'image/png', 'application/x-mediahub-tag', 'gallery']) {
      expect(isGalleryMime(m)).toBe(false);
    }
  });
});

describe('no scattered gallery mime literals', () => {
  const FRONTEND = path.resolve(__dirname, '..');
  const SKIP_DIRS = new Set(['node_modules', 'dist', 'coverage', 'public']);
  const SELF = path.join(__dirname, 'galleryMime.ts');

  function* sources(dir: string): Generator<string> {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (ent.name.startsWith('.')) continue;
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (!SKIP_DIRS.has(ent.name)) yield* sources(full);
      } else if (/\.(ts|tsx)$/.test(ent.name)) {
        yield full;
      }
    }
  }

  const E2E_DIRS = ['e2e', 'e2e-prod'];
  const isE2e = (file: string): boolean =>
    E2E_DIRS.includes(path.relative(FRONTEND, file).split(path.sep)[0]);

  const isTest = (file: string): boolean =>
    /\.test\.(ts|tsx)$/.test(file) || /\.spec\.(ts|tsx)$/.test(file);

  // Every gallery check must go through this module — a stray literal is a
  // site that would have to be found by hand at the next rename. Test files
  // may spell the current value (fixtures of real rows).
  it('only galleryMime.ts spells out the gallery mime in app code', () => {
    const offenders: string[] = [];
    for (const file of sources(FRONTEND)) {
      if (file === SELF || isTest(file) || isE2e(file)) continue;
      if (fs.readFileSync(file, 'utf8').includes(NEW)) {
        offenders.push(path.relative(FRONTEND, file));
      }
    }
    expect(offenders).toEqual([]);
  });

  // The legacy spelling is banned everywhere, tests and e2e included: no row
  // carries it any more, so a fixture using it describes a gallery that cannot
  // exist — and after step 3 it would silently be a non-gallery, testing the
  // wrong branch while still reading as "a gallery row".
  it('nothing spells the retired legacy gallery mime', () => {
    const offenders: string[] = [];
    for (const file of sources(FRONTEND)) {
      if (fs.readFileSync(file, 'utf8').includes(OLD)) {
        offenders.push(path.relative(FRONTEND, file));
      }
    }
    expect(offenders).toEqual([]);
  });
});
