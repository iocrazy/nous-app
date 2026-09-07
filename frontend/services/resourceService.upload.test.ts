/**
 * `uploadResource` — the progress samples it emits, the typed failures it
 * raises, and the cancel that really stops the bytes.
 *
 * ⚠️ EVERY ASSERTION HERE IS POSITIVE (`toBe(...)` / `toEqual(...)`).
 *
 * "The upload did not report a wrong rate" is satisfied by an upload that
 * reports nothing at all, and "it did not throw the wrong error" is satisfied
 * by one that never throws. So what is pinned is the exact sample stream and
 * the exact `reason` / `status` / `detail` on the error — each of which fails
 * both when the value is wrong AND when the path stopped running.
 *
 * ⚠️ WHY A HAND-BUILT XHR DOUBLE RATHER THAN A LIBRARY.
 *
 * `fetch` cannot report upload progress at all — that is the entire reason the
 * XHR path exists — so there is no way to exercise this with the `fetch` stub
 * every other service test uses. The double below implements only the surface
 * `uploadResource` actually touches, and each test drives the events itself,
 * which is also what makes the ordering assertions possible.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../supabaseClient', () => ({ supabase: {} }));
vi.mock('../utils/apiConfig', () => ({ getApiUrl: () => 'http://api.test' }));
vi.mock('./parserService', () => ({
  getAuthHeaders: async () => ({ Authorization: 'Bearer T' }),
}));

import {
  ResourceUploadError,
  classifyUploadStatus,
  uploadResource,
  type UploadProgressSample,
} from './resourceService';

/** The slice of XMLHttpRequest this function uses, and nothing else. */
class FakeXhr {
  static last: FakeXhr | null = null;

  status = 0;

  responseText = '';

  aborted = false;

  sent: FormData | null = null;

  url = '';

  readonly headers: Record<string, string> = {};

  private readonly listeners: Record<string, Array<() => void>> = {};

  readonly upload = {
    listeners: [] as Array<(e: ProgressEvent) => void>,
    addEventListener(_type: string, fn: (e: ProgressEvent) => void) {
      this.listeners.push(fn);
    },
  };

  constructor() { FakeXhr.last = this; }

  addEventListener(type: string, fn: () => void) {
    (this.listeners[type] ??= []).push(fn);
  }

  open(_method: string, url: string) { this.url = url; }

  setRequestHeader(k: string, v: string) { this.headers[k] = v; }

  send(body: FormData) { this.sent = body; }

  abort() {
    this.aborted = true;
    this.fire('abort');
  }

  fire(type: string) { (this.listeners[type] ?? []).forEach((fn) => { fn(); }); }

  progress(loaded: number, total: number, lengthComputable = true) {
    this.upload.listeners.forEach((fn) => {
      fn({ loaded, total, lengthComputable } as ProgressEvent);
    });
  }

  finish(status: number, body: string) {
    this.status = status;
    this.responseText = body;
    this.fire('load');
  }
}

beforeEach(() => {
  FakeXhr.last = null;
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const file = () => new File(['payload'], 'shot.jpg', { type: 'image/jpeg' });

describe('the progress samples', () => {
  /**
   * ⚠️ THE POINT OF THE WHOLE CHANGE.
   *
   * The callback used to hand over a rounded PERCENTAGE, which is lossy in
   * exactly the direction that matters: no caller could recover bytes from it,
   * so no caller could ever compute a rate no matter how it smoothed. Bytes
   * plus a clock reading is the minimum from which a rate is derivable at all.
   */
  it('reports bytes and a clock reading, not a percentage', async () => {
    const samples: UploadProgressSample[] = [];
    /* Pinned immediately before each event rather than queued up front: the
       clock is read by other things in between, so a `mockReturnValueOnce`
       chain hands its first value to whoever asks first — which is not this
       function. */
    const now = vi.spyOn(Date, 'now');

    const done = uploadResource(file(), 'scope-1', undefined, (s) => samples.push(s));
    await Promise.resolve();

    now.mockReturnValue(1_000);
    FakeXhr.last?.progress(2_048, 8_192);
    now.mockReturnValue(1_500);
    FakeXhr.last?.progress(6_144, 8_192);
    FakeXhr.last?.finish(200, JSON.stringify({ data: { id: 'r-1' } }));

    expect(await done).toEqual({ id: 'r-1' });
    expect(samples).toEqual([
      { loaded: 2_048, total: 8_192, at: 1_000 },
      { loaded: 6_144, total: 8_192, at: 1_500 },
    ]);
  });

  /**
   * `total` is the request body's size as the browser sees it. When the
   * browser says it cannot compute one, the honest answer is null — NOT a
   * quiet substitution of `file.size`, which is a different number (the body
   * carries multipart framing too) that the browser never agreed to.
   */
  it('says the total is unknown rather than substituting the file size', async () => {
    const samples: UploadProgressSample[] = [];
    vi.spyOn(Date, 'now').mockReturnValue(4_000);

    const done = uploadResource(file(), 'scope-1', undefined, (s) => samples.push(s));
    await Promise.resolve();

    FakeXhr.last?.progress(512, 0, false);
    FakeXhr.last?.finish(200, JSON.stringify({ data: { id: 'r-1' } }));
    await done;

    expect(samples).toEqual([{ loaded: 512, total: null, at: 4_000 }]);
  });
});

describe('typed failures', () => {
  /** The table on its own — it is a table, and a table only ever exercised
   *  through a mocked transport is a table nobody checks. */
  it('maps statuses to reasons', () => {
    expect(classifyUploadStatus(401)).toBe('unauthorized');
    expect(classifyUploadStatus(403)).toBe('unauthorized');
    expect(classifyUploadStatus(413)).toBe('too_large');
    expect(classifyUploadStatus(400)).toBe('rejected');
    expect(classifyUploadStatus(404)).toBe('rejected');
    expect(classifyUploadStatus(500)).toBe('server');
    expect(classifyUploadStatus(503)).toBe('server');
  });

  /**
   * ⚠️ The sentence this replaces was `new Error('Failed to upload resource')`
   * for every non-2xx — on the one path where the backend HAD said something
   * specific. `413 File too large. Maximum size is 500 MB.` was arriving and
   * being thrown away.
   */
  it('carries the reason, the status and the backend’s own words', async () => {
    const done = uploadResource(file(), 'scope-1', undefined, () => {});
    await Promise.resolve();
    FakeXhr.last?.finish(413, JSON.stringify({ detail: 'File too large. Maximum size is 500 MB.' }));

    const err = await done.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ResourceUploadError);
    const e = err as ResourceUploadError;
    /* ⚠️ CONTRACT, not decoration. `PublishPage` recognises this error by its
       `name` rather than with `instanceof` — see `describeUploadFailure` for
       why crossing a module boundary to compare constructors is the check that
       quietly stops holding. Renaming the class without this line would leave
       every upload failure on that page classified as "unknown", silently. */
    expect(e.name).toBe('ResourceUploadError');
    expect(e.reason).toBe('too_large');
    expect(e.status).toBe(413);
    expect(e.detail).toBe('File too large. Maximum size is 500 MB.');
    // The message is the backend's sentence, so a caller that only logs
    // `err.message` still gets something that says which thing went wrong.
    expect(e.message).toBe('File too large. Maximum size is 500 MB.');
  });

  /**
   * 2026-09-07: an object-store write failure answers 503 through the
   * AppError handler, whose envelope is `{error, code, request_id}` — not
   * FastAPI's `{detail}`. The user sentence lives in `error`; it must reach
   * `detail`/`message` the same way a `detail` would, or the one failure the
   * backend now explains would be the one shown as "Upload failed (server)".
   */
  it('reads the AppError envelope (`error`) when there is no `detail`', async () => {
    const done = uploadResource(file(), 'scope-1', undefined, () => {});
    await Promise.resolve();
    FakeXhr.last?.finish(
      503,
      JSON.stringify({
        success: false,
        error: 'Storage is unavailable, so nothing was saved. Try again in a moment.',
        code: 'object_store_write_failed',
        request_id: 'r1',
      }),
    );

    const e = (await done.catch((err: unknown) => err)) as ResourceUploadError;
    expect(e.reason).toBe('server');
    expect(e.status).toBe(503);
    expect(e.detail).toBe('Storage is unavailable, so nothing was saved. Try again in a moment.');
    expect(e.message).toBe('Storage is unavailable, so nothing was saved. Try again in a moment.');
  });

  it('says the detail is absent rather than inventing one', async () => {
    const done = uploadResource(file(), 'scope-1', undefined, () => {});
    await Promise.resolve();
    FakeXhr.last?.finish(500, 'not json at all');

    const e = await done.catch((err: unknown) => err) as ResourceUploadError;
    expect(e.reason).toBe('server');
    expect(e.status).toBe(500);
    expect(e.detail).toBe(null);
  });

  it('names a transport failure as one, with no status to report', async () => {
    const done = uploadResource(file(), 'scope-1', undefined, () => {});
    await Promise.resolve();
    FakeXhr.last?.fire('error');

    const e = await done.catch((err: unknown) => err) as ResourceUploadError;
    expect(e.reason).toBe('network');
    expect(e.status).toBe(null);
  });

  /**
   * A 2xx whose body will not parse is its own outcome. Before, the
   * `JSON.parse` threw from inside a promise executor, which is a
   * `SyntaxError` escaping through a path nobody was catching.
   */
  it('names an unreadable success as its own failure', async () => {
    const done = uploadResource(file(), 'scope-1', undefined, () => {});
    await Promise.resolve();
    FakeXhr.last?.finish(200, '<html>proxy says hello</html>');

    const e = await done.catch((err: unknown) => err) as ResourceUploadError;
    expect(e.reason).toBe('malformed');
    expect(e.status).toBe(200);
  });
});

describe('cancelling', () => {
  /**
   * ⚠️ THE ONE THAT MATTERS FOR A CANCEL BUTTON.
   *
   * A cancel that only hides the panel while the request runs on is not a
   * cancel, it is a picture of one. So the assertion is that the REQUEST was
   * aborted, not merely that the promise rejected.
   */
  it('aborts the request itself, and rejects as aborted', async () => {
    const controller = new AbortController();
    const done = uploadResource(file(), 'scope-1', undefined, () => {}, undefined, controller.signal);
    await Promise.resolve();
    expect(FakeXhr.last?.sent).toBeInstanceOf(FormData);

    controller.abort();

    expect(FakeXhr.last?.aborted).toBe(true);
    const e = await done.catch((err: unknown) => err) as ResourceUploadError;
    expect(e.reason).toBe('aborted');
  });

  /**
   * A signal that is already aborted must stop the upload BEFORE the bytes go
   * out. Wiring only the event would send the whole file and cancel it a
   * moment later — the on-the-wire version of the bug the signal prevents.
   */
  it('never sends anything when the signal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();

    const e = await uploadResource(
      file(), 'scope-1', undefined, () => {}, undefined, controller.signal,
    ).catch((err: unknown) => err) as ResourceUploadError;

    expect(e.reason).toBe('aborted');
    // Positive: the double was constructed (so the path really ran) and it was
    // never given a body — rather than "sent is not a FormData", which would
    // also hold if the request had never been created.
    expect(FakeXhr.last?.sent).toBe(null);
  });
});
