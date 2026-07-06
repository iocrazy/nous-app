/**
 * Unit tests for the scene/chapter → flow-graph projection (Phase B Task 1).
 * Pins the six semantics from the plan's Task 1 contract: dual node types,
 * persisted-coordinate priority, per-chapter auto-layout, edge generation,
 * orphan scenes, and input immutability.
 */

import { describe, expect, it } from 'vitest';
import { mapToFlow, sceneSummary } from '../nodes/sceneNodeMapper';
import type { SceneDoc, ScriptElement } from '../types';
import type { ScriptChapter } from '../../types';

function makeChapter(over: Partial<ScriptChapter> & { id: string }): ScriptChapter {
  return {
    script_id: 's1',
    chapter_number: 1,
    title: 'Chapter',
    position_x: 0,
    position_y: 0,
    data_json: {},
    sort_order: 0,
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
    ...over,
  };
}

function el(over: Partial<ScriptElement> & { id: string }): ScriptElement {
  return { type: 'action', text: '', ...over };
}

function makeScene(over: Partial<SceneDoc> & { id: string }): SceneDoc {
  return {
    script_id: 's1',
    chapter_id: null,
    heading_int_ext: 'INT',
    location_text: 'Studio',
    time_of_day: 'NIGHT',
    content_version: 1,
    elements: [],
    sort_order: 0,
    position_x: null,
    position_y: null,
    ...over,
  };
}

describe('sceneNodeMapper.mapToFlow', () => {
  it('emits chapter nodes (ch-<id>, chapterNode) mirroring ChapterNodeData shape', () => {
    const chapters = [
      makeChapter({
        id: '100',
        chapter_number: 2,
        title: 'Act One',
        summary: 'sum',
        content: 'body',
        branch_label: 'if rain',
        branch_type: 'condition',
        position_x: 40,
        position_y: 80,
      }),
    ];
    const { nodes } = mapToFlow([], chapters);
    const chapter = nodes.find((n) => n.id === 'ch-100');
    expect(chapter).toBeDefined();
    expect(chapter!.type).toBe('chapterNode');
    expect(chapter!.position).toEqual({ x: 40, y: 80 });
    expect(chapter!.data).toEqual({
      title: 'Act One',
      summary: 'sum',
      content: 'body',
      chapterNumber: 2,
      branchLabel: 'if rain',
      branchType: 'condition',
      contentJson: null,
    });
  });

  it('emits scene nodes (sc-<id>, sceneNode) with first-action summary truncated to 60 chars', () => {
    const longText = 'A'.repeat(80);
    const scenes = [
      makeScene({
        id: '200',
        elements: [
          el({ id: 'e1', type: 'character', text: 'JOHN' }),
          el({ id: 'e2', type: 'action', text: '   ' }),
          el({ id: 'e3', type: 'action', text: longText }),
        ],
      }),
    ];
    const { nodes } = mapToFlow(scenes, []);
    const scene = nodes.find((n) => n.id === 'sc-200');
    expect(scene).toBeDefined();
    expect(scene!.type).toBe('sceneNode');
    const data = scene!.data as { scene: SceneDoc; summary: string };
    expect(data.scene.id).toBe('200');
    expect(data.summary).toBe('A'.repeat(60) + '…');
    // Direct helper: short action text is returned verbatim without ellipsis.
    expect(sceneSummary(makeScene({ id: 'x', elements: [el({ id: 'e', text: 'Rain falls.' })] }))).toBe(
      'Rain falls.',
    );
  });

  it('uses persisted position_x/y when both are non-null', () => {
    const scenes = [makeScene({ id: '200', chapter_id: '100', position_x: 555, position_y: 666 })];
    const chapters = [makeChapter({ id: '100', position_x: 10, position_y: 20 })];
    const { nodes } = mapToFlow(scenes, chapters);
    const scene = nodes.find((n) => n.id === 'sc-200')!;
    expect(scene.position).toEqual({ x: 555, y: 666 });
  });

  it('auto-lays scenes in a per-chapter column when coordinates are null', () => {
    const chapters = [makeChapter({ id: '100', position_x: 300, position_y: 0 })];
    const scenes = [
      makeScene({ id: '201', chapter_id: '100' }),
      makeScene({ id: '202', chapter_id: '100' }),
    ];
    const { nodes } = mapToFlow(scenes, chapters);
    // x = chapter.position_x (300) + 320; y = index * 140 within the group.
    expect(nodes.find((n) => n.id === 'sc-201')!.position).toEqual({ x: 620, y: 0 });
    expect(nodes.find((n) => n.id === 'sc-202')!.position).toEqual({ x: 620, y: 140 });
  });

  it('generates ch→sc edges and chapter→chapter parent edges', () => {
    const chapters = [
      makeChapter({ id: '100', position_x: 0, position_y: 0 }),
      makeChapter({ id: '101', parent_chapter_id: '100', position_x: 400, position_y: 0 }),
    ];
    const scenes = [makeScene({ id: '200', chapter_id: '101' })];
    const { edges } = mapToFlow(scenes, chapters);
    expect(edges).toContainEqual({
      id: 'edge-ch-101-sc-200',
      source: 'ch-101',
      target: 'sc-200',
    });
    expect(edges).toContainEqual({
      id: 'edge-ch-100-ch-101',
      source: 'ch-100',
      target: 'ch-101',
    });
  });

  it('still produces a node for an orphan (chapterless) scene, columned from origin', () => {
    const scenes = [
      makeScene({ id: '300', chapter_id: null }),
      makeScene({ id: '301', chapter_id: null }),
    ];
    const { nodes, edges } = mapToFlow(scenes, []);
    expect(nodes.find((n) => n.id === 'sc-300')!.position).toEqual({ x: 0, y: 0 });
    expect(nodes.find((n) => n.id === 'sc-301')!.position).toEqual({ x: 0, y: 140 });
    // No edge for a chapterless scene.
    expect(edges).toHaveLength(0);
  });

  it('threads a shot cover url onto the matching scene node only (Task 4)', () => {
    const scenes = [makeScene({ id: '200' }), makeScene({ id: '201' })];
    const covers = new Map([['200', '/api/v1/generated-media/9/cover']]);
    const { nodes } = mapToFlow(scenes, [], covers);
    const s200 = nodes.find((n) => n.id === 'sc-200')!.data as { coverUrl?: string };
    const s201 = nodes.find((n) => n.id === 'sc-201')!.data as { coverUrl?: string };
    expect(s200.coverUrl).toBe('/api/v1/generated-media/9/cover');
    // Scenes without a cover entry carry no coverUrl at all (zero-regression).
    expect('coverUrl' in s201).toBe(false);
  });

  it('never mutates its inputs and returns only string ids', () => {
    const chapters = [makeChapter({ id: '100' })];
    const scenes = [makeScene({ id: '200', chapter_id: '100' })];
    const chaptersSnapshot = JSON.parse(JSON.stringify(chapters));
    const scenesSnapshot = JSON.parse(JSON.stringify(scenes));

    const { nodes, edges } = mapToFlow(scenes, chapters);

    expect(chapters).toEqual(chaptersSnapshot);
    expect(scenes).toEqual(scenesSnapshot);
    for (const n of nodes) expect(typeof n.id).toBe('string');
    for (const e of edges) {
      expect(typeof e.id).toBe('string');
      expect(typeof e.source).toBe('string');
      expect(typeof e.target).toBe('string');
    }
  });
});
