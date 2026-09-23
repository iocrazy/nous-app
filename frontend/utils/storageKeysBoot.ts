// frontend/utils/storageKeysBoot.ts
//
// Side-effect module: imported FIRST in index.tsx so the mediahub → nous
// storage-key migration finishes before any other module evaluates (ES
// modules evaluate in import order, depth first). Keep its only import the
// dependency-free `./storageKeys`.
import { migrateLegacyStorageKeys } from './storageKeys';

migrateLegacyStorageKeys();
