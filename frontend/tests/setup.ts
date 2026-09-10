// Global Vitest setup. Stub browser globals that jsdom doesn't cover
// so services under test don't accidentally talk to the real network.

import '@testing-library/jest-dom';
import { afterEach, beforeEach, vi } from 'vitest';

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

// jsdom implements neither `Range.getClientRects` nor `Element.getClientRects`
// on every node type, and ProseMirror calls them while scrolling a selection
// into view — from a transaction, so the TypeError surfaces as an UNHANDLED
// exception rather than a test failure. The tests still pass; vitest reports
// "N errors" and exits non-zero, which reads as a red build with nothing red
// in it. Real browsers have the API (the e2e suite exercises the same code
// paths), so this is an environment gap, not product behaviour worth mocking
// away in a way that could hide a genuine layout bug.
const emptyRect = (): DOMRect =>
  ({
    x: 0, y: 0, width: 0, height: 0,
    top: 0, right: 0, bottom: 0, left: 0,
    toJSON: () => ({}),
  }) as DOMRect;

for (const proto of [Range.prototype, Element.prototype] as const) {
  if (typeof (proto as { getClientRects?: unknown }).getClientRects !== 'function') {
    Object.defineProperty(proto, 'getClientRects', {
      value: () => Object.assign([], { item: () => null, length: 0 }),
      writable: true,
      configurable: true,
    });
  }
  if (typeof (proto as { getBoundingClientRect?: unknown }).getBoundingClientRect !== 'function') {
    Object.defineProperty(proto, 'getBoundingClientRect', {
      value: emptyRect,
      writable: true,
      configurable: true,
    });
  }
}

// A unit test touching the network is itself the bug. An un-mocked fetch
// rejects SYNCHRONOUSLY so the failure lands in the test that started it,
// instead of settling after jsdom is gone and surfacing as an
// EnvironmentTeardownError attributed to a file with nothing failing in it
// (2026-09-10, AISettings.codexDaemon). Tests that install their own fetch
// mock simply overwrite this.
const disabledFetch = (): typeof fetch =>
  vi.fn((input: RequestInfo | URL) =>
    Promise.reject(new Error(`fetch is disabled in unit tests: ${String(input)}`)),
  ) as unknown as typeof fetch;

// Per test, not once: the afterEach below runs vi.restoreAllMocks(), which
// would hand the native fetch back to whichever test ran next.
beforeEach(() => {
  Object.defineProperty(globalThis, 'fetch', {
    value: disabledFetch(),
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  // Cheap reset — keeps the shim alive but isolates test state.
  localStorageShim.clear();
  sessionStorageShim.clear();
});
