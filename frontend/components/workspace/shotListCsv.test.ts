import { describe, expect, it } from 'vitest';
import { buildShotListCsv, type ShotListCsvGroup } from './shotListCsv';

const shot = (over: Partial<ShotListCsvGroup['shots'][number]> = {}) => ({
  shot_number: 1,
  shot_type: 'WIDE',
  camera_angle: 'EYE',
  camera_movement: 'STATIC',
  focal_length: '35mm',
  description: 'Establishing shot.',
  ...over,
});

describe('buildShotListCsv', () => {
  it('starts with a UTF-8 BOM and the RFC4180 header row', () => {
    const csv = buildShotListCsv([]);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv.slice(1)).toBe('Scene,Shot,Type,Angle,Movement,Lens,Description');
  });

  it('emits one row per shot with the resolved scene label repeated', () => {
    const groups: ShotListCsvGroup[] = [
      { sceneLabel: '1', shots: [shot({ shot_number: 1 }), shot({ shot_number: 2 })] },
      { sceneLabel: '2', shots: [shot({ shot_number: 1 })] },
    ];
    const rows = buildShotListCsv(groups).slice(1).split('\r\n');
    expect(rows).toHaveLength(4); // header + 3 shots
    expect(rows[1]).toBe('1,1,WIDE,EYE,STATIC,35mm,Establishing shot.');
    expect(rows[2]).toBe('1,2,WIDE,EYE,STATIC,35mm,Establishing shot.');
    expect(rows[3]).toBe('2,1,WIDE,EYE,STATIC,35mm,Establishing shot.');
  });

  it('contributes no data rows for an empty group (scene with zero shots)', () => {
    const groups: ShotListCsvGroup[] = [
      { sceneLabel: '1', shots: [] },
      { sceneLabel: '2', shots: [shot()] },
    ];
    const rows = buildShotListCsv(groups).slice(1).split('\r\n');
    expect(rows).toHaveLength(2); // header + 1 shot from scene 2 only
  });

  it('quotes fields containing a comma, doubling embedded quotes', () => {
    const groups: ShotListCsvGroup[] = [
      { sceneLabel: '1', shots: [shot({ description: 'Wide, then a "push in".' })] },
    ];
    const rows = buildShotListCsv(groups).slice(1).split('\r\n');
    expect(rows[1]).toBe('1,1,WIDE,EYE,STATIC,35mm,"Wide, then a ""push in""."');
  });

  it('quotes fields containing an embedded newline', () => {
    const groups: ShotListCsvGroup[] = [
      { sceneLabel: '1', shots: [shot({ description: 'Line one\nLine two' })] },
    ];
    const rows = buildShotListCsv(groups).slice(1).split('\r\n');
    // The quoted field's embedded \n must NOT be mistaken for a row boundary.
    expect(rows).toHaveLength(2);
    expect(rows[1]).toBe('1,1,WIDE,EYE,STATIC,35mm,"Line one\nLine two"');
  });

  it('renders null shot fields as empty CSV cells, not the literal "null"', () => {
    const groups: ShotListCsvGroup[] = [
      {
        sceneLabel: '3',
        shots: [
          shot({
            shot_number: null,
            shot_type: null,
            camera_angle: null,
            camera_movement: null,
            focal_length: null,
            description: null,
          }),
        ],
      },
    ];
    const rows = buildShotListCsv(groups).slice(1).split('\r\n');
    expect(rows[1]).toBe('3,,,,,,');
  });
});
