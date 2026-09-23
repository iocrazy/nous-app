// frontend/utils/storageKeysBoot.ts
//
// Side-effect module: imported FIRST in index.tsx so the mediahub → nous
// storage-key migration finishes before any other module evaluates (ES
// modules evaluate in import order, depth first). Keep its only import the
// dependency-free `./storageKeys`.
//
// Order matters: migrate (copy old → new, then write the marker), THEN purge.
// The purge only touches a storage whose marker was written, and only removes
// an old key whose new key holds a value.
import { migrateLegacyStorageKeys, purgeLegacyStorageKeys } from './storageKeys';

migrateLegacyStorageKeys();
purgeLegacyStorageKeys();
