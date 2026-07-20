/**
 * IssueFlowStrip — renders the flow-progress dots for a stage-mirror issue.
 * Verifies dot state counts, position text (row vs detail), the empty-catalog
 * no-render, and the project-workspace link target.
 */

import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { IssueFlowStrip } from './IssueFlowStrip';
import type { ProjectStage } from '../../types';

function stage(id: string, slug: string, order: number): ProjectStage {
  return { id, slug, name: slug, sort_order: order, tools_recommended: [] };
}

const CATALOG: ProjectStage[] = [
  stage('10', 'planning', 1),
  stage('20', 'script', 2),
  stage('30', 'storyboard', 3),
  stage('40', 'render', 4),
  stage('50', 'publish', 5),
  stage('60', 'archive', 6),
];

function renderStrip(props: Partial<React.ComponentProps<typeof IssueFlowStrip>> = {}) {
  return render(
    <MemoryRouter>
      <IssueFlowStrip
        catalog={CATALOG}
        currentStageId="30"
        to="/team/7/projects/500"
        {...props}
      />
    </MemoryRouter>,
  );
}

describe('IssueFlowStrip', () => {
  it('renders one dot per stage with the right done/current/future split', () => {
    renderStrip(); // current = index 2 (storyboard) → 2 done, 1 current, 3 future
    expect(screen.getAllByTestId('flow-dot-done')).toHaveLength(2);
    expect(screen.getAllByTestId('flow-dot-current')).toHaveLength(1);
    expect(screen.getAllByTestId('flow-dot-future')).toHaveLength(3);
  });

  it('shows the 1-based x/y position for the row variant', () => {
    renderStrip();
    expect(screen.getByTestId('issue-flow-strip')).toHaveTextContent('3/6');
  });

  it('prefixes the current stage name for the detail variant', () => {
    renderStrip({ variant: 'detail' });
    expect(screen.getByTestId('issue-flow-strip')).toHaveTextContent('storyboard · 3/6');
  });

  it('links to the project workspace', () => {
    renderStrip();
    expect(screen.getByTestId('issue-flow-strip')).toHaveAttribute(
      'href',
      '/team/7/projects/500',
    );
  });

  it('exposes each stage name as a dot title for hover', () => {
    renderStrip();
    expect(screen.getByTitle('storyboard')).toBeInTheDocument();
  });

  it('renders nothing while the catalog is empty (not yet loaded / non-mirror)', () => {
    renderStrip({ catalog: [] });
    expect(screen.queryByTestId('issue-flow-strip')).toBeNull();
  });
});
