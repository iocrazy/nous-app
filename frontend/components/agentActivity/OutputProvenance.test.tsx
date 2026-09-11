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
 * BIGINTs), `versions` newest first, `title` nullable, and NO `deep_link` or
 * issue key, because that endpoint does not carry one.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

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
import { OutputsError } from '../../services/outputsService';

afterEach(cleanup);

const version = (v: number, over: Record<string, unknown> = {}) => ({
  id: `7271452993825340${10 + v}`,
  version: v,
  parent_version: v > 1 ? v - 1 : null,
  run_id: '727145299382534100',
  issue_id: '727145299382534000',
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
});

const renderBlock = (props: Partial<React.ComponentProps<typeof OutputProvenance>> = {}) =>
  render(<OutputProvenance kind="script_shot" refId="727145299382534999" {...props} />);

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

  it('links to the issue only when the host could resolve a link', async () => {
    renderBlock({ issueHref: '/team/9/todolist/MH-91' });
    const link = await screen.findByTestId('output-provenance-issue');
    expect(link.getAttribute('href')).toBe('/team/9/todolist/MH-91');
  });

  it('never builds an issue URL out of an issue_id', async () => {
    // The lineage response carries `issue_id` and no key or deep link. The
    // route is `/team/:teamId/todolist/:identifier`, keyed by the issue KEY —
    // a URL assembled from the snowflake would 404 or, worse, land on some
    // other issue. Absent beats invented.
    renderBlock();
    await screen.findByTestId('output-provenance');
    const link = screen.queryByTestId('output-provenance-issue');
    expect(link?.getAttribute('href') ?? null).toBeNull();
    expect(screen.getByTestId('output-provenance').innerHTML).not.toContain('727145299382534000');
  });

  it('re-reads when it is pointed at a different object', async () => {
    const { rerender } = renderBlock();
    await screen.findByTestId('output-provenance');
    rerender(<OutputProvenance kind="script_shot" refId="727145299382534777" />);
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
