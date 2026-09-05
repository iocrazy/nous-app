/**
 * Result-body registry (harness P4 T11): one component per `ResultKind`.
 * TaskDetailModal asks `resultBodyFor(kind)` instead of branching; a new kind
 * = a new entry here + a case in taskResultKind. Enumerable so a test can
 * assert every kind has a body — an unmapped kind renders the generic body,
 * never a blank pane.
 */
import type React from 'react';

import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import type { ResultKind } from '../taskResultKind';
import { AgentResultBody } from './AgentResultBody';
import { CanvasGenResultBody } from './CanvasGenResultBody';
import { CoverFramesResultBody } from './CoverFramesResultBody';
import { GenericResultBody } from './GenericResultBody';
import { MediaResultBody } from './MediaResultBody';
import { TextResultBody } from './TextResultBody';
import { VisionResultBody } from './VisionResultBody';

/** Every body takes the same props; each reads what it needs. */
export interface ResultBodyProps {
  task: UnifiedTask;
  kind: ResultKind;
  data: unknown;
  onOpenResource: (resourceId: string) => void;
}

type Body = React.FC<ResultBodyProps>;

const MediaBody: Body = ({ task, data, onOpenResource }) => (
  <MediaResultBody task={task} resource={data as never} onOpenResource={onOpenResource} />
);
const AgentBody: Body = ({ task }) => <AgentResultBody task={task} />;
const TextBody: Body = ({ kind, data }) => <TextResultBody kind={kind as 'transcript' | 'summary'} data={data as never} />;
const VisionBody: Body = ({ task, data }) => <VisionResultBody task={task} data={data as never} />;
const CanvasGenBody: Body = ({ task }) => <CanvasGenResultBody task={task} />;
const CoverFramesBody: Body = ({ task }) => <CoverFramesResultBody task={task} />;
const GenericBody: Body = ({ task }) => <GenericResultBody task={task} />;

const REGISTRY: Record<ResultKind, Body> = {
  media: MediaBody,
  agent: AgentBody,
  transcript: TextBody,
  summary: TextBody,
  vision: VisionBody,
  canvasGen: CanvasGenBody,
  coverFrames: CoverFramesBody,
  generic: GenericBody,
};

export function resultBodyFor(kind: ResultKind): Body {
  return REGISTRY[kind] ?? GenericBody;
}

export function registeredResultKinds(): ResultKind[] {
  return Object.keys(REGISTRY).sort() as ResultKind[];
}
