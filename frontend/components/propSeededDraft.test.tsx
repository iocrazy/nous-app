/**
 * Prop-seeded draft fields must re-seed DURING RENDER, not from a `useEffect`.
 *
 * ── Why this file exists ──────────────────────────────────────────────────
 * Several panels keep a local draft for an inline-editable field and commit it
 * on blur only if it differs from the prop it was seeded from. If the seed runs
 * in a `useEffect` it is a DEFERRED write: React commits the DOM first and
 * flushes passive effects in a later scheduler task. Under CPU contention that
 * seed can flush in the SAME batch as a keystroke and, landing after it,
 * silently overwrite what the user just typed — the draft snaps back to the
 * server value, the blur commit compares equal, and NO update is sent. The edit
 * is lost with zero user-visible feedback.
 *
 * ── Why the obvious test does not work ────────────────────────────────────
 * A plain "type then blur, expect onUpdate" test passes on BOTH versions:
 * Testing Library wraps every `fireEvent` in `act()`, which flushes pending
 * passive effects, so the broken ordering never occurs. On the real bug the
 * symptom is an intermittently red assertion reading `Number of calls: 0` —
 * the callback was never invoked, not invoked late, so no `waitFor` or larger
 * timeout can fix it (and adding one only converts a false red into a false
 * green).
 *
 * ── The discriminator used here ───────────────────────────────────────────
 * `flushSync` commits synchronously but does NOT flush passive effects. So
 * immediately after a `flushSync` re-render with a changed prop:
 *   - render-phase seeding  → the input already shows the new prop value
 *   - useEffect seeding     → the input still shows the OLD draft
 * That difference is deterministic on every machine, which is what makes this
 * a real regression gate rather than a race we hope to lose.
 *
 * We drive React directly (`createRoot` + `flushSync`) instead of Testing
 * Library's `render`/`rerender`, because those wrap in `act()` and would flush
 * the effects we are trying to observe.
 *
 * ── Reproducing the underlying race (kept here so nobody re-derives it) ───
 * Steady CPU starvation barely triggers it — uniform slowdown delays every
 * task equally. Irregular preemption does. Run the suite serially (parallel
 * campaigns pull each other's timing flat and the yield drops to zero):
 *
 *   ( npx vitest run <file> ) & vp=$!
 *   ( while kill -0 "$vp" 2>/dev/null; do
 *       kill -STOP "$vp"; sleep 0.00$((RANDOM % 9 + 1))
 *       kill -CONT "$vp"; sleep 0.0$((RANDOM % 3 + 1))
 *     done ) & jp=$!
 *   wait "$vp"; kill "$jp" 2>/dev/null
 *
 * Observed base rate on the unfixed code was ~1% per run, so budget a few
 * hundred runs before drawing conclusions from a green result.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { flushSync } from 'react-dom';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string, d?: string) => d ?? k }),
}));

vi.mock('../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
}));

vi.mock('./EagleTagPicker', () => ({ EagleTagPicker: () => <div /> }));
vi.mock('./resources/ResourcePromptSection', () => ({
  ResourcePromptSection: () => <div />,
}));

// MobileAudioUpload hands its notes draft down to the shell; stub the shell to
// surface just that prop so the same-commit assertion has something to read.
vi.mock('./MobileAudioShell', () => ({
  MobileAudioShell: ({ notes }: { notes?: string }) => (
    <div data-testid="shell-notes">{notes}</div>
  ),
}));

vi.mock('./Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../services/lyricsService', () => ({ getResourceLyrics: vi.fn() }));

vi.mock('../services/projectsService', () => ({
  updateProject: vi.fn().mockResolvedValue({}),
  deleteProject: vi.fn(),
}));

vi.mock('../contexts/IslandWorkContext', () => ({
  useIslandWork: () => ({
    infoIslandEl: null,
    setInfoAvailable: () => {},
    setInfoVisible: () => {},
  }),
}));

import { FolderInfoPanel } from './FolderInfoPanel';
import { ResourceInfoPanel } from './ResourceInfoPanel';
import { MobileAudioUpload } from './MobileAudioUpload';
import { ProjectSettingsPanel } from './ProjectSettingsPanel';

const folder = (over: Record<string, unknown> = {}) =>
  ({ id: 'f1', name: 'Alpha', color: null, created_at: null, ...over }) as never;

const resource = (over: Record<string, unknown> = {}) =>
  ({
    id: 'r1',
    creator_id: 'u1',
    source_type: 'upload',
    media_id: null,
    filename: 'a.png',
    file_type: 'image',
    mime_type: 'image/png',
    notes: null,
    url: null,
    rating: 0,
    is_trashed: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...over,
  }) as never;

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  // We drive React directly rather than through Testing Library, so the act
  // environment flag has to be set by hand.
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

/** Mount inside act() so the initial mount's effects DO flush (steady state). */
function mount(node: React.ReactElement) {
  act(() => root.render(node));
}

/**
 * Re-render with flushSync: the DOM is committed synchronously, passive effects
 * are NOT flushed. Anything asserted right after this observes the render-phase
 * result only.
 */
function commitWithoutFlushingEffects(node: React.ReactElement) {
  flushSync(() => root.render(node));
}

describe('inline-edit drafts re-seed during render, not in an effect', () => {
  it('FolderInfoPanel: a renamed folder lands in the input in the same commit', () => {
    const onRename = vi.fn();
    mount(
      <FolderInfoPanel folder={folder()} onClose={() => {}} onRename={onRename} />,
    );

    // Enter edit mode (the input is click-to-edit here).
    const pencilRow = container.querySelector('.group') as HTMLElement;
    act(() => pencilRow.click());
    const input = container.querySelector('input') as HTMLInputElement;
    expect(input.value).toBe('Alpha');

    commitWithoutFlushingEffects(
      <FolderInfoPanel
        folder={folder({ name: 'Renamed Elsewhere' })}
        onClose={() => {}}
        onRename={onRename}
      />,
    );

    // The seed also leaves edit mode, so on a render-phase seed the input is
    // already gone in this very commit and the heading shows the new name.
    // With a useEffect seed both writes are still pending here: the input would
    // still be mounted and still read 'Alpha' — that deferred window is exactly
    // where a keystroke gets overwritten.
    expect(container.querySelector('input')).toBeNull();
    expect(container.textContent).toContain('Renamed Elsewhere');
  });

  it('ResourceInfoPanel: changed notes land in the textarea in the same commit', () => {
    const props = {
      allTags: [],
      assignedTags: [],
      readOnly: false,
      onClose: () => {},
      onAddTag: () => {},
      onRemoveTag: () => {},
      onUpdate: () => {},
    };
    mount(<ResourceInfoPanel resource={resource({ notes: 'first note' })} {...props} />);

    const textarea = container.querySelector('textarea') as HTMLTextAreaElement;
    expect(textarea.value).toBe('first note');

    commitWithoutFlushingEffects(
      <ResourceInfoPanel resource={resource({ notes: 'second note' })} {...props} />,
    );

    const after = container.querySelector('textarea') as HTMLTextAreaElement;
    expect(after.value).toBe('second note');
  });

  it('ResourceInfoPanel: a changed url lands in the input in the same commit', () => {
    const props = {
      allTags: [],
      assignedTags: [],
      readOnly: false,
      onClose: () => {},
      onAddTag: () => {},
      onRemoveTag: () => {},
      onUpdate: () => {},
    };
    mount(
      <ResourceInfoPanel
        resource={resource({ url: 'https://example.test/one' })}
        {...props}
      />,
    );

    const urlInput = container.querySelector(
      'input[value="https://example.test/one"]',
    ) as HTMLInputElement | null;
    expect(urlInput?.value ?? '').toBe('https://example.test/one');

    commitWithoutFlushingEffects(
      <ResourceInfoPanel
        resource={resource({ url: 'https://example.test/two' })}
        {...props}
      />,
    );

    const values = Array.from(container.querySelectorAll('input')).map((el) => el.value);
    expect(values).toContain('https://example.test/two');
  });

  it('MobileAudioUpload: changed notes reach the shell in the same commit', () => {
    const props = {
      fileUrl: 'https://api.test/audio',
      onBack: () => {},
    };
    mount(
      <MobileAudioUpload resource={resource({ notes: 'note one' })} {...props} />,
    );
    expect(container.querySelector('[data-testid="shell-notes"]')?.textContent).toBe(
      'note one',
    );

    commitWithoutFlushingEffects(
      <MobileAudioUpload resource={resource({ notes: 'note two' })} {...props} />,
    );

    expect(container.querySelector('[data-testid="shell-notes"]')?.textContent).toBe(
      'note two',
    );
  });

  // This one commits through a Save button gated on `hasChanges`, not on blur,
  // so the deferred-seed symptom is slightly different: the typed value is
  // reverted AND the button re-arms to disabled, leaving the edit unrecoverable
  // rather than merely unsent. Same cause, same fix.
  it('ProjectSettingsPanel: a changed project lands in the form in the same commit', () => {
    const project = (over: Record<string, unknown> = {}) =>
      ({
        id: 'p1',
        name: 'First Project',
        announcement: null,
        project_type: 'general',
        project_group: null,
        ...over,
      }) as never;

    const props = {
      isOpen: true,
      onClose: () => {},
      onUpdated: () => {},
      onDeleted: () => {},
    };
    mount(<ProjectSettingsPanel project={project()} {...props} />);
    const nameInput = container.querySelector('input') as HTMLInputElement;
    expect(nameInput.value).toBe('First Project');

    commitWithoutFlushingEffects(
      <ProjectSettingsPanel project={project({ name: 'Renamed Project' })} {...props} />,
    );

    const after = container.querySelector('input') as HTMLInputElement;
    expect(after.value).toBe('Renamed Project');
  });
});

/**
 * ResourceDetailPage carries the same three fields (filename / notes / url) and
 * got the same fix, but it is a full page: 43 imports, router + auth + supabase
 * + a dozen services. Mounting it here would cost more mock surface than the
 * assertion is worth, and a mock-heavy mount tends to rot into a test that
 * passes for the wrong reason.
 *
 * So this one gets a narrower gate: assert the seed is the render-phase guard
 * and that the old effect-with-deps form has not come back. It is a weaker
 * check than the behavioural ones above — it pins the shape, not the effect —
 * but it is deterministic and it does go red on a revert, which is the point.
 * If this page ever grows a cheap render harness, replace this with the same
 * `commitWithoutFlushingEffects` assertion the others use.
 */
describe('ResourceDetailPage seeds its draft fields during render', () => {
  it('uses the render-phase guard and not a useEffect seed', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    // vitest runs with the frontend package root as cwd.
    const src = readFileSync(
      resolve(process.cwd(), 'components/ResourceDetailPage.tsx'),
      'utf8',
    );

    expect(src).toContain('if (seed != null && resource && seedChanged) {');
    // The exact dep array the old deferred seed used.
    expect(src).not.toContain(
      '}, [resource?.id, resource?.filename, resource?.notes, resource?.url]);',
    );
  });
});
