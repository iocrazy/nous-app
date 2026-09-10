/**
 * The three times a person actually means by "later" (harness 2b-2 §5-2).
 * Pure, and computed from a passed-in `now` so the tests are not at the
 * mercy of the clock.
 *
 * Tonight disappears once it has gone by: the backend rejects a `fire_at` in
 * the past (`fire_at_in_past`), and a button that can only fail is worse
 * than no button.
 */
export interface WakeupPreset {
  key: 'in1h' | 'tonight' | 'tomorrow';
  labelKey: string;
  fallback: string;
  at: Date;
}

/** Local wall-clock time on the day `now` falls in, shifted by `days`. */
function atLocal(now: Date, days: number, hours: number, minutes: number): Date {
  const d = new Date(now);
  d.setDate(d.getDate() + days);
  d.setHours(hours, minutes, 0, 0);
  return d;
}

export function wakeupPresets(now: Date): WakeupPreset[] {
  const presets: WakeupPreset[] = [
    { key: 'in1h', labelKey: 'later.in1h', fallback: 'In 1 Hour', at: new Date(now.getTime() + 3600_000) },
  ];
  const tonight = atLocal(now, 0, 20, 0);
  if (tonight.getTime() > now.getTime()) {
    presets.push({ key: 'tonight', labelKey: 'later.tonight', fallback: 'Tonight 20:00', at: tonight });
  }
  presets.push({ key: 'tomorrow', labelKey: 'later.tomorrow', fallback: 'Tomorrow 09:00', at: atLocal(now, 1, 9, 0) });
  return presets;
}
