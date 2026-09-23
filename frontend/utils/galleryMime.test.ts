/**
 * Gallery MIME rename, step 1 of 2: the new spelling is the one we write, and
 * every classification accepts BOTH spellings — rows written before the data
 * migration still carry the legacy value, and old bundles stay open.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  GALLERY_MIME,
  GALLERY_MIMES,
  LEGACY_GALLERY_MIME,
  isGalleryMime,
} from './galleryMime';

const NEW = 'application/x-nous-gallery';
const OLD = 'application/x-mediahub-gallery';

describe('galleryMime', () => {
  it('writes the new spelling', () => {
    expect(GALLERY_MIME).toBe(NEW);
    expect(LEGACY_GALLERY_MIME).toBe(OLD);
  });

  it('accepts both spellings', () => {
    expect([...GALLERY_MIMES].sort()).toEqual([OLD, NEW].sort());
    expect(isGalleryMime(NEW)).toBe(true);
    expect(isGalleryMime(OLD)).toBe(true);
  });

  it('rejects everything else', () => {
    for (const m of [null, undefined, '', 'image/png', 'application/x-mediahub-tag', 'gallery']) {
      expect(isGalleryMime(m)).toBe(false);
    }
  });
});

// Every gallery check must go through this module — a stray literal is a site
// that recognises only one spelling. Test files may use literals (fixtures).
describe('no scattered gallery mime literals', () => {
  const FRONTEND = path.resolve(__dirname, '..');
  const SKIP_DIRS = new Set(['node_modules', 'dist', 'coverage', 'e2e', 'e2e-prod', 'public']);
  const SELF = path.join(__dirname, 'galleryMime.ts');

  function* sources(dir: string): Generator<string> {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      if (ent.name.startsWith('.')) continue;
      const full = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        if (!SKIP_DIRS.has(ent.name)) yield* sources(full);
      } else if (/\.(ts|tsx)$/.test(ent.name) && !/\.test\.(ts|tsx)$/.test(ent.name)) {
        yield full;
      }
    }
  }

  it('only galleryMime.ts spells out the gallery mime', () => {
    const offenders: string[] = [];
    for (const file of sources(FRONTEND)) {
      if (file === SELF) continue;
      const src = fs.readFileSync(file, 'utf8');
      if (src.includes('x-mediahub-gallery') || src.includes('x-nous-gallery')) {
        offenders.push(path.relative(FRONTEND, file));
      }
    }
    expect(offenders).toEqual([]);
  });
});
