// Global Vitest setup. Stub browser globals that jsdom doesn't cover
// so services under test don't accidentally talk to the real network.

import { afterEach, vi } from 'vitest';

// Storage polyfill — vitest 4.1.x + jsdom 29.x gives us an empty `{}`
// (null prototype) for `localStorage`/`sessionStorage` instead of a real
// Storage instance, so any `.clear()`/`.getItem()`/`.setItem()` call
// throws "is not a function". Install an in-memory Storage so tests that
// touch web storage (e.g. services/apiClient.test.ts) behave like a real
// browser. The afterEach below resets contents per test.
class MemoryStorage implements Storage {
  private store = new Map<string, string>();
  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

const localStorageShim = new MemoryStorage();
const sessionStorageShim = new MemoryStorage();

const targets: Array<Window & typeof globalThis> = [globalThis as Window & typeof globalThis];
if (typeof window !== 'undefined') {
  targets.push(window as Window & typeof globalThis);
}
for (const target of targets) {
  Object.defineProperty(target, 'localStorage', {
    value: localStorageShim,
    writable: true,
    configurable: true,
  });
  Object.defineProperty(target, 'sessionStorage', {
    value: sessionStorageShim,
    writable: true,
    configurable: true,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  // Cheap reset — keeps the shim alive but isolates test state.
  localStorageShim.clear();
  sessionStorageShim.clear();
});
