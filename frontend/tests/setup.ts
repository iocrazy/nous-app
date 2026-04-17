// Global Vitest setup. Stub browser globals that jsdom doesn't cover
// so services under test don't accidentally talk to the real network.

import { afterEach, vi } from 'vitest';

afterEach(() => {
  vi.restoreAllMocks();
});
