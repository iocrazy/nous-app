/**
 * "Where did this come from?" on an object's own page (harness 3a Task 6).
 *
 * The load-bearing case is the FIRST one: an object nothing registered is a
 * human-made object, and a human-made object has no provenance. That is not an
 * error and must not render as one — most shots, scenes and resources in the
 * library were made by a person, so a block that said "could not read" on all
 * of them would be a permanent false alarm on every page.
 *
 * Everything else is the opposite rule: a read that genuinely failed says so.
 *
 * The service is mocked at the module boundary and answers the REAL wire shape
 * of `GET /api/v1/outputs/{kind}/{ref_id}` — ids as strings (Snowflake
 * BIGINTs), `versions` newest first, `title` nullable, and since 3a Task 3b
 * every version carrying `issue_key` and a finished `deep_link`, both `null`
 * when the run answers to no issue. The backend never assembles that URL out
 * of `issue_id`, and neither does this block: an absent link stays absent.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

const { getOutputLineage } = vi.hoisted(() => ({ getOutputLineage: vi.fn() }));

vi.mock('../../services/outputsService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../services/outputsService')>();
  return { ...actual, getOutputLineage };
});

// The dialog does its own fetching; this file is about the block that opens it.
vi.mock('../Todolist/OutputDiffDialog', () => ({
  OutputDiffDialog: (props: { kind: string; refId: string }) => (
    <div data-testid="output-diff-dialog" data-ref={props.refId} />
  ),
}));

import { OutputProvenance } from './OutputProvenance';
import { invalidateOutputLineage, OutputsError } from '../../services/outputsService';

afterEach(cleanup);

const version = (v: number, over: Record<string, unknown> = {}) => ({
  id: `7271452993825340${10 + v}`,
  version: v,
  parent_version: v > 1 ? v - 1 : null,
  run_id: '727145299382534100',
  issue_id: '727145299382534000',
  issue_key: 'MH-91',
  actor_user_id: null,
  reverted_from_version: null,
  cost_kind: 'exact' as const,
  deep_link: '/team/424242424242/todolist/MH-91?step=4',
  seq: null,
  turn: null,
  step: 4,
  title: 'S3 · Shot #1',
  model: 'qwen-max',
  cost_cents: 12,
  created_at: '2026-09-10T08:30:00Z',
  ...over,
});

const lineage = (versions: ReturnType<typeof version>[]) => ({
  kind: 'script_shot',
  ref_id: '727145299382534999',
  latest_version: versions[0].version,
  versions,
  // A Snowflake id, so a STRING on the wire — never a number.
  as_of_seq: '727145299382534770',
});

const OBJECT_ROUTE = '/team/424242424242/canvas/5';

/**
 * The block under its REAL routing context: it mounts inside the canvas
 * editor and the script sheet, which are themselves routes. Both destinations
 * are mounted so that "did this navigate in-app" is a fact the DOM can state —
 * a bare `<a href>` would print the same href and go nowhere in jsdom.
 */
const harness = (props: Partial<React.ComponentProps<typeof OutputProvenance>> = {}) => (
  <MemoryRouter initialEntries={[OBJECT_ROUTE]}>
    <Routes>
      <Route
        path="/team/:teamId/canvas/:canvasId"
        element={<OutputProvenance kind="script_shot" refId="727145299382534999" {...props} />}
      />
      <Route path="/team/:teamId/todolist/:identifier" element={<div data-testid="issue-page" />} />
    </Routes>
  </MemoryRouter>
);

const renderBlock = (props: Partial<React.ComponentProps<typeof OutputProvenance>> = {}) =>
  render(harness(props));

beforeEach(() => {
  getOutputLineage.mockReset();
  getOutputLineage.mockResolvedValue(lineage([version(2), version(1)]));
});

describe('OutputProvenance', () => {
  it('renders nothing for an object no run registered', async () => {
    // 404 `not_registered` means "a person made this", which is the normal
    // state of most objects in the library — not a failure to report.
    getOutputLineage.mockRejectedValue(new OutputsError('not_registered', 404, 'not in the registry'));
    const { container } = renderBlock();
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalled());
    await waitFor(() => expect(container.querySelector('[data-testid="output-provenance"]')).toBeNull());
    expect(screen.queryByTestId('output-provenance-error')).toBeNull();
  });

  it('says so when the read failed for any other reason', async () => {
    // Silence here would be indistinguishable from "a person made this" — the
    // one confusion this block must never create.
    getOutputLineage.mockRejectedValue(new OutputsError('http_500', 500, 'boom'));
    renderBlock();
    expect(await screen.findByTestId('output-provenance-error')).toBeTruthy();
    // Our words, not the server's.
    expect(screen.getByTestId('output-provenance-error').textContent).not.toContain('boom');
  });

  it('renders nothing at all while the read is in flight', () => {
    // A skeleton would flash on every human-made object, which is most of
    // them, before resolving to nothing.
    const { container } = renderBlock();
    expect(container.querySelector('[data-testid="output-provenance"]')).toBeNull();
  });

  it('names the run and the step that produced the latest version', async () => {
    renderBlock();
    const block = await screen.findByTestId('output-provenance');
    expect(block.getAttribute('data-run')).toBe('727145299382534100');
    // The attribute rather than the sentence: the words go through i18n, and
    // this suite runs against an uninitialised instance that does not
    // interpolate. The fact under test is that the STEP travelled, not how it
    // is worded.
    expect(block.getAttribute('data-step')).toBe('4');
  });

  it('counts the versions, so a revised object says it was revised', async () => {
    renderBlock();
    const block = await screen.findByTestId('output-provenance');
    expect(block.getAttribute('data-versions')).toBe('2');
  });

  it('offers Diff only once there are two versions to compare', async () => {
    renderBlock();
    expect(await screen.findByTestId('output-provenance-diff')).toBeTruthy();
  });

  it('hides Diff on a single-version object rather than disabling it', async () => {
    // One version has nothing to compare against; a control that exists and
    // can never work reads as broken.
    getOutputLineage.mockResolvedValue(lineage([version(1)]));
    renderBlock();
    await screen.findByTestId('output-provenance');
    expect(screen.queryByTestId('output-provenance-diff')).toBeNull();
  });

  it('opens the version dialog for this object', async () => {
    renderBlock();
    fireEvent.click(await screen.findByTestId('output-provenance-diff'));
    expect((await screen.findByTestId('output-diff-dialog')).getAttribute('data-ref')).toBe(
      '727145299382534999',
    );
  });

  it('follows the lineage\u2019s own deep link when the host supplies none', async () => {
    // Task 3b put a finished URL on every version. The object pages this
    // block ships on know nothing about issues, so without this the link is
    // permanently disabled on exactly the objects that HAVE an issue.
    renderBlock();
    const link = await screen.findByTestId('output-provenance-issue');
    expect(link.getAttribute('href')).toBe('/team/424242424242/todolist/MH-91?step=4');
    expect(screen.queryByTestId('output-provenance-issue-unlinked')).toBeNull();
  });

  it('navigates in-app rather than reloading the document', async () => {
    // The href alone cannot tell these apart — a bare `<a>` prints the same
    // string. What separates them is what a click DOES: this block mounts
    // inside the canvas editor, and a document reload there throws away the
    // graph the reader is standing in. Clicking must reach the issue route
    // without leaving the app.
    renderBlock();
    fireEvent.click(await screen.findByTestId('output-provenance-issue'));
    expect(await screen.findByTestId('issue-page')).toBeTruthy();
  });

  it('lets a host that knows better override the lineage\u2019s link', async () => {
    // The prop stays an override rather than a fallback: a host mounted ON an
    // issue page already knows which issue the reader came from.
    renderBlock({ issueHref: '/team/9/todolist/MH-7' });
    const link = await screen.findByTestId('output-provenance-issue');
    expect(link.getAttribute('href')).toBe('/team/9/todolist/MH-7');
  });

  it('never builds an issue URL out of an issue_id', async () => {
    // A run with no issue — or an issue with no key or no team — comes back
    // with `deep_link: null`, and the block leaves it null. The route is
    // `/team/:teamId/todolist/:identifier`, keyed by the issue KEY; a URL
    // assembled from the snowflake would 404 or, worse, land on some other
    // issue. Absent beats invented.
    getOutputLineage.mockResolvedValue(
      lineage([version(2, { issue_key: null, deep_link: null }), version(1, { issue_key: null, deep_link: null })]),
    );
    renderBlock();
    await screen.findByTestId('output-provenance');
    expect(screen.queryByTestId('output-provenance-issue')).toBeNull();
    const disabled = screen.getByTestId('output-provenance-issue-unlinked');
    expect(disabled.getAttribute('title')).toBeTruthy();
    expect(screen.getByTestId('output-provenance').innerHTML).not.toContain('727145299382534000');
  });

  it('re-reads when it is pointed at a different object', async () => {
    const { rerender } = renderBlock();
    await screen.findByTestId('output-provenance');
    rerender(
      <MemoryRouter initialEntries={[OBJECT_ROUTE]}>
        <Routes>
          <Route
            path="/team/:teamId/canvas/:canvasId"
            element={<OutputProvenance kind="script_shot" refId="727145299382534777" />}
          />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(2));
    expect(getOutputLineage).toHaveBeenLastCalledWith('script_shot', '727145299382534777');
  });

  it('asks nothing when it has no object to ask about', () => {
    // Panels render before their row arrives; a request for `undefined` would
    // be a 400 per empty panel open.
    renderBlock({ refId: '' });
    expect(getOutputLineage).not.toHaveBeenCalled();
  });
});

/**
 * harness 3b Task 6 — the block re-reads when its chain is invalidated.
 *
 * This block mounts on pages with no WebSocket and no polling (a canvas node,
 * a library panel), so the ONLY thing that can tell it the chain moved is the
 * cache generation the service bumps.
 */
describe('OutputProvenance — live refresh', () => {
  it('re-reads after the lineage is invalidated', async () => {
    getOutputLineage.mockResolvedValue(lineage([version(2), version(1)]));
    render(
      <MemoryRouter>
        <OutputProvenance kind="script_shot" refId="9" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(1));
    act(() => invalidateOutputLineage('script_shot', '9'));
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(2));
  });

  it('re-reads ONCE when a done frame invalidates three objects', async () => {
    // A run registers three objects, so the `done` frame drops three keys —
    // three generation bumps inside one dispatch. React coalesces them into a
    // single render, so a consumer re-reads once. Were they to arrive as three
    // separate renders, every consumer on the page would multiply each run's
    // output count by its own re-read.
    getOutputLineage.mockResolvedValue(lineage([version(2), version(1)]));
    render(
      <MemoryRouter>
        <OutputProvenance kind="script_shot" refId="9" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(1));

    act(() => {
      for (const [kind, id] of [['script_shot', '9'], ['script_scene', '3'], ['generated_media', '77']]) {
        invalidateOutputLineage(kind, id);
      }
    });
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(2));
    await new Promise((r) => setTimeout(r, 20));
    expect(getOutputLineage).toHaveBeenCalledTimes(2);
  });

  it('does not close an open version dialog when some OTHER object changes', async () => {
    // The generation is global: every invalidate anywhere moves it. A re-read
    // is cheap (this object's own entry is still cached), but tearing the
    // component's view state down with it would shut the dialog under the
    // reader's hands the moment an unrelated agent run finished.
    getOutputLineage.mockResolvedValue(lineage([version(2), version(1)]));
    render(
      <MemoryRouter>
        <OutputProvenance kind="script_shot" refId="9" />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByTestId('output-provenance-diff'));
    expect(await screen.findByTestId('output-diff-dialog')).toBeTruthy();

    act(() => invalidateOutputLineage('generated_media', '77'));
    await waitFor(() => expect(getOutputLineage).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('output-diff-dialog')).not.toBeNull();
  });
});
