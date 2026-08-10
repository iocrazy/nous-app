/**
 * buildShotListCsv — pure RFC4180 encoder for the shot-list table's CSV
 * export (三视图 Task 2). Deliberately decoupled from `SceneDoc`/`Shot`
 * (sceneService.ts) so it stays a trivial pure function to test: the caller
 * (EpisodeShotListTable) already resolves the scene label (real
 * `scene_number` vs. 1-based fallback, same as EpisodeSceneBoard's
 * `sceneNumberLabel`) before handing rows in here.
 *
 * UTF-8 BOM prefix + CRLF row endings so Excel opens CJK content correctly
 * instead of mangling it as Latin-1.
 */

export interface ShotListCsvShot {
  shot_number: number | null;
  shot_type: string | null;
  camera_angle: string | null;
  camera_movement: string | null;
  focal_length: string | null;
  description: string | null;
}

export interface ShotListCsvGroup {
  /** Already-resolved scene label (see EpisodeShotListTable's sceneNumberLabel). */
  sceneLabel: string;
  shots: ShotListCsvShot[];
}

const CSV_HEADERS = ['Scene', 'Shot', 'Type', 'Angle', 'Movement', 'Lens', 'Description'];

/** RFC4180: quote a field iff it contains a comma, quote, or newline; double any embedded quotes. */
function escapeCsvField(value: string): string {
  if (/[",\r\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

function csvRow(fields: string[]): string {
  return fields.map(escapeCsvField).join(',');
}

export function buildShotListCsv(groups: ShotListCsvGroup[]): string {
  const rows = [csvRow(CSV_HEADERS)];
  for (const group of groups) {
    for (const shot of group.shots) {
      rows.push(
        csvRow([
          group.sceneLabel,
          shot.shot_number != null ? String(shot.shot_number) : '',
          shot.shot_type ?? '',
          shot.camera_angle ?? '',
          shot.camera_movement ?? '',
          shot.focal_length ?? '',
          shot.description ?? '',
        ]),
      );
    }
  }
  // Empty groups (scenes with zero shots) contribute no data rows — the CSV
  // is a flat per-shot table, unlike the UI table's group-header rows.
  const BOM = '﻿';
  return BOM + rows.join('\r\n');
}
