/**
 * DateTimePopover — the ONE date / date-time control in this app.
 *
 * It replaces four parallel implementations that had each grown half of the
 * feature set:
 *   - `components/common/DateRangePopover.tsx`  ranges, no time  (this file, generalised)
 *   - `components/DateTimePicker.tsx`           a point in time, hardcoded English (deleted)
 *   - `components/workflow/NodeSchedulePicker.tsx` react-day-picker wrapper (deleted)
 *   - `<input type="datetime-local">` in Distribution/PublishPage (replaced)
 * The native input was the worst of the four: in a zh-CN browser it renders as
 * `mm/dd/yyyy, --:-- --`, which no amount of i18n on our side can fix.
 *
 * Shape: a calendar shell (month grid + nav + footer) with an OPTIONAL time
 * column pair bolted on the right — the split borrowed from gotion's
 * DatePickerModal/TimePickerColumn pair, re-implemented here because that one
 * rides `date-fns` + `motion/react` and this repo has neither.
 *
 * Two selection modes off one shell:
 *   mode="range"  (default) — 'YYYY-MM-DD' start/end, commit-on-complete.
 *   mode="single"           — one 'YYYY-MM-DD', or 'YYYY-MM-DDTHH:mm' with
 *                             `withTime`. That second shape is deliberately the
 *                             `datetime-local` wire format, so callers that used
 *                             the native input keep their existing value math.
 *
 * Window enforcement (`minAt` / `maxAt`): every day, hour and minute outside the
 * window renders `disabled`. This is the point of the prop — a bounded picker
 * that lets you select an illegal value and then scolds you has not helped
 * anyone. Picking a day whose currently-held time would fall outside the window
 * SNAPS the time to that day's first legal slot rather than committing an
 * illegal instant. Month nav is disabled past the window's edges for the same
 * reason.
 *
 * Rendered through `createPortal(document.body)` with `position: fixed` off the
 * anchor's `getBoundingClientRect()`, so it floats above any scroll container.
 * Placement is below the anchor +6px, flipping above when the popover's
 * *measured* height (`offsetHeight`, not an estimate) would overflow the
 * viewport bottom — re-measured on every month change since only layout knows
 * the true height.
 *
 * Range selection is commit-on-complete: the first click only buffers a draft
 * `start` (visually selected, no `onChange` — a caller that treats `onChange` as
 * "apply this" must never see a half-open range). The second click completes the
 * range and is the one that calls `onChange(start, end)` (earlier date wins
 * `start`). Escape / outside-click / scroll close without calling `onChange`,
 * discarding any mid-flight draft.
 *
 * Range ALSO has a segment-edit mode, because "nudge the deadline by two days"
 * should not cost a full re-pick of both ends. The Start / End cells in the
 * footer are buttons: pressing one arms that end (`editing`), the next day click
 * writes ONLY that end and commits `onChange(start, end)` with the other end
 * untouched, then disarms back to the two-click flow. Pressing the armed cell
 * again cancels. `editing === null` — the default on every open — is the
 * original two-click behaviour, unchanged.
 *
 * Clear obeys the same arming: with a segment armed it empties ONLY that end
 * (and says so — the label becomes "Clear start"/"Clear end"), leaving the
 * other end alone. Half-bounded ranges are a real user need ("added after
 * Aug 1, no upper bound") and the two native inputs this control replaced
 * could each be emptied on their own; unarmed, Clear still wipes both.
 *
 * While a segment is armed, days that would invert the range (a start after the
 * held end, or an end before the held start) render `disabled`, exactly like the
 * `minAt`/`maxAt` window does. The tempting alternative — accept the click and
 * quietly drag the OTHER end along — is rejected on purpose: the end the user
 * did not touch is not ours to move, and a picker that silently rewrites a field
 * you were not editing is worse than one that greys out the impossible.
 *
 * Single selection commits on every interaction (day, hour, minute) because its
 * callers drive live validation off the value; `withTime` keeps the popover open
 * so the time is still reachable, and closes on Done.
 *
 * Dates are strings throughout the public props — no `Date` objects cross the
 * boundary except the window bounds, which are instants by nature. Internal Date
 * math is fine since ISO-8601 date strings sort lexically in calendar order,
 * which several comparisons below lean on directly.
 */

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';

export interface MonthCell {
  /** Local Date at noon (avoids DST/midnight edge flips). */
  date: Date;
  /** 'YYYY-MM-DD'. */
  iso: string;
  /** Whether this cell belongs to the month being rendered (vs a leading/trailing filler day). */
  inMonth: boolean;
}

interface BaseProps {
  anchorEl: HTMLElement | null; // null = closed
  onClose: () => void;
  /**
   * Selectable window, inclusive. Anything outside renders `disabled` — days,
   * hours and minutes alike. Omitted/null = unbounded on that side.
   */
  minAt?: Date | null;
  maxAt?: Date | null;
}

export interface DateRangeMode extends BaseProps {
  mode?: 'range';
  start: string | null; // 'YYYY-MM-DD'
  end: string | null;
  onChange: (start: string | null, end: string | null) => void; // clear = (null, null)
}

export interface DateSingleMode extends BaseProps {
  mode: 'single';
  /** 'YYYY-MM-DD', or 'YYYY-MM-DDTHH:mm' when `withTime`. */
  value: string | null;
  /** Attach the hour/minute columns and widen the value to `...THH:mm`. */
  withTime?: boolean;
  /** Show the Today / Tomorrow / This Sunday / In 3 Days shortcut row. */
  quickOptions?: boolean;
  onChange: (value: string | null) => void; // clear = null
}

export type DateTimePopoverProps = DateRangeMode | DateSingleMode;

// The month grid is 7 columns of `w-8` cells with `gap-1` between them, so the
// calendar column needs a hard 7*32 + 6*4 = 248px of *content* box, and the
// popover adds `p-3` (12px a side) around it.
//
// These are not decorative numbers: CALENDAR_WIDTH was 260 on first release,
// which left the grid 236px — twelve short. Grid columns then divided the
// deficit among themselves, the `w-8` buttons overflowed their tracks, and the
// month rendered as one run-on smear ("26272829303 1"). `DAY_CELL`/`DAY_GAP`
// exist so the relationship is stated once and checked by a test rather than
// re-derived by whoever next changes a cell size.
const DAY_CELL = 32; // Tailwind `w-8`/`h-8` on the day buttons
const DAY_GAP = 4; // Tailwind `gap-1` between grid tracks
const POPOVER_PAD = 12; // Tailwind `p-3` on the popover shell
export const CALENDAR_GRID_WIDTH = DAY_CELL * 7 + DAY_GAP * 6;
const CALENDAR_WIDTH = CALENDAR_GRID_WIDTH + POPOVER_PAD * 2;
const TIME_WIDTH = 104;
/** Minutes are offered on a 5-minute grid — 60 rows of scroll is not a control. */
const MINUTE_STEP = 5;
const HOURS: number[] = Array.from({ length: 24 }, (_, h) => h);
const MINUTES: number[] = Array.from({ length: 60 / MINUTE_STEP }, (_, i) => i * MINUTE_STEP);
const DAY_MS = 86_400_000;

const WEEKDAY_KEYS = [
  ['common.dateRangePopover.weekdaySun', 'Su'],
  ['common.dateRangePopover.weekdayMon', 'Mo'],
  ['common.dateRangePopover.weekdayTue', 'Tu'],
  ['common.dateRangePopover.weekdayWed', 'We'],
  ['common.dateRangePopover.weekdayThu', 'Th'],
  ['common.dateRangePopover.weekdayFri', 'Fr'],
  ['common.dateRangePopover.weekdaySat', 'Sa'],
] as const;

const pad2 = (n: number): string => String(n).padStart(2, '0');

/** 'YYYY-MM-DD' → local Date (noon). Returns null for null/invalid input. */
function parseISO(s: string | null): Date | null {
  if (!s) return null;
  const [y, m, d] = s.split('-').map(Number);
  if (!y || !m || !d) return null;
  return new Date(y, m - 1, d, 12);
}

export function fmtISO(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

/**
 * Split a public value into its date and time halves. Accepts both the
 * date-only and the `datetime-local` shapes; anything else reads as "nothing
 * selected" rather than throwing, because these values arrive from stored rows.
 */
export function splitValue(v: string | null): {
  date: string | null;
  hour: number | null;
  minute: number | null;
} {
  const none = { date: null, hour: null, minute: null };
  if (!v) return none;
  const [datePart, timePart] = v.split('T');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return none;
  if (!timePart) return { date: datePart, hour: null, minute: null };
  const [h, m] = timePart.split(':').map(Number);
  return {
    date: datePart,
    hour: Number.isFinite(h) ? h : null,
    minute: Number.isFinite(m) ? m : null,
  };
}

/** Local wall-clock instant for a day + time-of-day. */
function instantOf(iso: string, hour: number, minute: number): number {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, hour, minute, 0, 0).getTime();
}

function inWindow(ms: number, min: number | null, max: number | null): boolean {
  if (min !== null && ms < min) return false;
  if (max !== null && ms > max) return false;
  return true;
}

/**
 * Whether ANY selectable instant inside this day is inside the window.
 *
 * With time columns the last reachable instant is 23:55, not 23:59:59.999 —
 * using the wall-clock end of day would leave a day enabled whose every actual
 * slot is illegal.
 */
export function dayAllowed(
  iso: string,
  min: number | null,
  max: number | null,
  withTime: boolean,
): boolean {
  const dayStart = instantOf(iso, 0, 0);
  const dayEnd = withTime ? instantOf(iso, 23, 60 - MINUTE_STEP) : dayStart + DAY_MS - 1;
  if (min !== null && dayEnd < min) return false;
  if (max !== null && dayStart > max) return false;
  return true;
}

/** The earliest legal (hour, minute) on a day, or null if the day has none. */
export function firstAllowedSlot(
  iso: string,
  min: number | null,
  max: number | null,
): { hour: number; minute: number } | null {
  for (const hour of HOURS) {
    for (const minute of MINUTES) {
      if (inWindow(instantOf(iso, hour, minute), min, max)) return { hour, minute };
    }
  }
  return null;
}

/**
 * Pure 6-week (42-cell) grid for a given year + 0-based month (JS `Date`
 * convention: 0 = January). Always returns 42 cells so the popover height is
 * stable across months — leading/trailing cells from the adjacent months
 * fill out the first/last rows and carry `inMonth: false`.
 */
export function monthGrid(year: number, month: number): MonthCell[] {
  const first = new Date(year, month, 1, 12);
  const gridStart = new Date(year, month, 1 - first.getDay(), 12);
  const cells: MonthCell[] = [];
  for (let i = 0; i < 42; i++) {
    const date = new Date(
      gridStart.getFullYear(),
      gridStart.getMonth(),
      gridStart.getDate() + i,
      12,
    );
    cells.push({ date, iso: fmtISO(date), inMonth: date.getMonth() === month });
  }
  return cells;
}

/** Shortcut targets, as day offsets resolved against "now". */
function quickTargets(now: Date): Array<{ key: string; def: string; iso: string }> {
  const day = (offset: number) =>
    fmtISO(new Date(now.getFullYear(), now.getMonth(), now.getDate() + offset, 12));
  // Strictly the NEXT Sunday — "This Sunday" on a Sunday means a week out, the
  // same call date-fns' nextSunday makes.
  const toSunday = 7 - now.getDay() || 7;
  return [
    { key: 'common.dateTimePopover.today', def: 'Today', iso: day(0) },
    { key: 'common.dateTimePopover.tomorrow', def: 'Tomorrow', iso: day(1) },
    { key: 'common.dateTimePopover.thisSunday', def: 'This Sunday', iso: day(toSunday) },
    { key: 'common.dateTimePopover.inThreeDays', def: 'In 3 Days', iso: day(3) },
  ];
}

export function DateTimePopover(props: DateTimePopoverProps): React.ReactPortal | null {
  const { anchorEl, onClose } = props;
  const isSingle = props.mode === 'single';
  const withTime = isSingle && props.withTime === true;
  const showQuick = isSingle && props.quickOptions === true;
  const minMs = props.minAt ? props.minAt.getTime() : null;
  const maxMs = props.maxAt ? props.maxAt.getTime() : null;

  // Narrowed reads of the mode-specific props. Pulled out here so the body
  // below never re-narrows the union at every use site.
  const rangeStart = props.mode === 'single' ? null : props.start;
  const rangeEnd = props.mode === 'single' ? null : props.end;
  const singleValue = props.mode === 'single' ? props.value : null;

  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const wasOpenRef = useRef(false);

  const [pendingStart, setPendingStart] = useState<string | null>(rangeStart);
  const [pendingEnd, setPendingEnd] = useState<string | null>(rangeEnd);
  /** Range only. null = the default two-click flow; otherwise the armed end. */
  const [editing, setEditing] = useState<'start' | 'end' | null>(null);
  const [selDate, setSelDate] = useState<string | null>(() => splitValue(singleValue).date);
  const [selHour, setSelHour] = useState(0);
  const [selMinute, setSelMinute] = useState(0);
  const [view, setView] = useState<{ year: number; month: number }>(() => {
    const base = parseISO(isSingle ? splitValue(singleValue).date : rangeStart) ?? new Date();
    return { year: base.getFullYear(), month: base.getMonth() };
  });

  // Resync from props whenever the popover transitions closed → open (a fresh
  // open should reflect the caller's current committed value and the month it
  // derives from — not whatever selection was mid-flight last time).
  useEffect(() => {
    const isOpen = !!anchorEl;
    if (isOpen && !wasOpenRef.current) {
      setPendingStart(rangeStart);
      setPendingEnd(rangeEnd);
      setEditing(null);
      const parsed = splitValue(singleValue);
      setSelDate(parsed.date);
      // With nothing committed yet the calendar opens on today, CLAMPED into
      // the window — opening on a month whose every cell is dead is how a
      // bounded picker looks broken. The time then defaults to that day's first
      // legal slot, never 00:00, which is the one instant a lead-time floor
      // reliably forbids.
      const now = new Date();
      let openIso = parsed.date;
      if (!openIso) {
        openIso = fmtISO(now);
        if (minMs !== null && instantOf(openIso, 23, 59) < minMs) {
          openIso = fmtISO(new Date(minMs));
        } else if (maxMs !== null && instantOf(openIso, 0, 0) > maxMs) {
          openIso = fmtISO(new Date(maxMs));
        }
      }
      const slot = firstAllowedSlot(openIso, minMs, maxMs);
      setSelHour(parsed.hour ?? slot?.hour ?? 9);
      setSelMinute(parsed.minute ?? slot?.minute ?? 0);
      const base = parseISO(isSingle ? openIso : rangeStart) ?? now;
      setView({ year: base.getFullYear(), month: base.getMonth() });
    }
    wasOpenRef.current = isOpen;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorEl]);

  const width = CALENDAR_WIDTH + (withTime ? TIME_WIDTH : 0);

  const place = useCallback(() => {
    if (!anchorEl || !ref.current) return;
    const r = anchorEl.getBoundingClientRect();
    const h = ref.current.offsetHeight;
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - 6 - h);
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 12));
    ref.current.style.top = `${top}px`;
    ref.current.style.left = `${left}px`;
  }, [anchorEl, width]);

  useLayoutEffect(place, [place, view.year, view.month]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    window.addEventListener('resize', place);
    return () => window.removeEventListener('resize', place);
  }, [anchorEl, place]);

  useEffect(() => {
    if (!anchorEl) return undefined;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current && !ref.current.contains(target) && !anchorEl.contains(target)) {
        onClose();
      }
    };
    const onScroll = (e: Event) => {
      // The time columns are scroll containers INSIDE the popover — scrolling
      // to 18:00 must not be read as "the page moved, close".
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    // Deferred so the click that opened the popover doesn't immediately close it.
    const timer = setTimeout(() => document.addEventListener('mousedown', onMouseDown), 0);
    window.addEventListener('scroll', onScroll, true);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      clearTimeout(timer);
      document.removeEventListener('mousedown', onMouseDown);
      window.removeEventListener('scroll', onScroll, true);
    };
  }, [anchorEl, onClose]);

  const gotoMonth = (delta: number) => {
    const total = view.year * 12 + view.month + delta;
    setView({ year: Math.floor(total / 12), month: ((total % 12) + 12) % 12 });
  };

  const emitSingle = (iso: string | null, hour: number, minute: number) => {
    if (props.mode !== 'single') return;
    if (iso === null) {
      props.onChange(null);
      return;
    }
    props.onChange(withTime ? `${iso}T${pad2(hour)}:${pad2(minute)}` : iso);
  };

  /**
   * Days the ARMED segment cannot legally take, because writing them would
   * invert the range against the end the user is not editing. Only ever true
   * while a segment is armed and the opposite end is actually set — with the
   * other end empty there is nothing to invert against, so the whole month is
   * fair game. ISO-8601 date strings sort lexically in calendar order.
   */
  const segmentBlocks = (iso: string): boolean => {
    if (editing === 'start') return !!pendingEnd && iso > pendingEnd;
    if (editing === 'end') return !!pendingStart && iso < pendingStart;
    return false;
  };

  const handleDayClick = (iso: string) => {
    if (!dayAllowed(iso, minMs, maxMs, withTime)) return;

    if (props.mode === 'single') {
      let hour = selHour;
      let minute = selMinute;
      if (withTime && !inWindow(instantOf(iso, hour, minute), minMs, maxMs)) {
        // The held time-of-day is illegal on the day just picked (typical when
        // picking "today" under a lead-time floor). Snap forward rather than
        // commit an instant the caller will only reject later.
        const slot = firstAllowedSlot(iso, minMs, maxMs);
        if (!slot) return;
        hour = slot.hour;
        minute = slot.minute;
      }
      setSelDate(iso);
      setSelHour(hour);
      setSelMinute(minute);
      emitSingle(iso, hour, minute);
      if (!withTime) onClose();
      return;
    }

    // Range, segment edit: write ONE end, leave the other exactly as it was,
    // commit, and fall back to the two-click flow.
    if (editing) {
      if (segmentBlocks(iso)) return;
      const s = editing === 'start' ? iso : pendingStart;
      const e = editing === 'end' ? iso : pendingEnd;
      setPendingStart(s);
      setPendingEnd(e);
      setEditing(null);
      props.onChange(s, e);
      return;
    }

    // Range: commit-on-complete (see the file header).
    if (!pendingStart || pendingEnd) {
      setPendingStart(iso);
      setPendingEnd(null);
      return;
    }
    const s = iso < pendingStart ? iso : pendingStart;
    const e = iso < pendingStart ? pendingStart : iso;
    setPendingStart(s);
    setPendingEnd(e);
    props.onChange(s, e);
  };

  const handleHourClick = (hour: number) => {
    let minute = selMinute;
    if (selDate && !inWindow(instantOf(selDate, hour, minute), minMs, maxMs)) {
      const next = MINUTES.find((m) => inWindow(instantOf(selDate, hour, m), minMs, maxMs));
      if (next === undefined) return;
      minute = next;
    }
    setSelHour(hour);
    setSelMinute(minute);
    if (selDate) emitSingle(selDate, hour, minute);
  };

  const handleMinuteClick = (minute: number) => {
    if (selDate && !inWindow(instantOf(selDate, selHour, minute), minMs, maxMs)) return;
    setSelMinute(minute);
    if (selDate) emitSingle(selDate, selHour, minute);
  };

  /**
   * Clear follows whatever the footer is currently armed for.
   *
   * With a segment armed, it clears ONLY that end and leaves the other exactly
   * as it is — the same "the end you are not editing is not ours to touch"
   * rule the day clicks follow. This is the only way to reach a half-bounded
   * filter ("added after Aug 1, no upper bound"); the two native date inputs
   * this control replaced could each be emptied on their own, and losing that
   * would have been a real regression, not a simplification. Unarmed, Clear
   * still wipes both ends, which is what an unqualified "Clear" should do.
   */
  const handleClear = () => {
    if (props.mode === 'single') {
      setSelDate(null);
      emitSingle(null, selHour, selMinute);
      return;
    }
    if (editing) {
      const s = editing === 'start' ? null : pendingStart;
      const e = editing === 'end' ? null : pendingEnd;
      setPendingStart(s);
      setPendingEnd(e);
      setEditing(null);
      props.onChange(s, e);
      return;
    }
    setPendingStart(null);
    setPendingEnd(null);
    setEditing(null);
    props.onChange(null, null);
  };

  /**
   * Arm / disarm a range end. Arming jumps the calendar to the month that end
   * already lives in — segment editing is overwhelmingly a small nudge, and
   * making the user page back to August to move an August date is the friction
   * this mode exists to remove. An empty end has no month to jump to, so the
   * current view stands.
   */
  const toggleSegment = (seg: 'start' | 'end') => {
    if (editing === seg) {
      setEditing(null);
      return;
    }
    setEditing(seg);
    const held = parseISO(seg === 'start' ? pendingStart : pendingEnd);
    if (held) setView({ year: held.getFullYear(), month: held.getMonth() });
  };

  if (!anchorEl) return null;

  const monthName = new Date(view.year, view.month, 1).toLocaleString('en-US', {
    month: 'long',
  });
  const monthYearLabel = t('common.dateRangePopover.monthYear', '{{monthName}} {{year}}', {
    monthName,
    monthNum: view.month + 1,
    year: view.year,
  });

  const cells = monthGrid(view.year, view.month);

  // Month nav past the window's edge would only show a grid of dead cells.
  const prevMonthEnd = new Date(view.year, view.month, 0, 23, 59, 59, 999).getTime();
  const nextMonthStart = new Date(view.year, view.month + 1, 1, 0, 0, 0, 0).getTime();
  const prevDisabled = minMs !== null && prevMonthEnd < minMs;
  const nextDisabled = maxMs !== null && nextMonthStart > maxMs;

  const hourDisabled = (hour: number): boolean =>
    !!selDate && !MINUTES.some((m) => inWindow(instantOf(selDate, hour, m), minMs, maxMs));
  const minuteDisabled = (minute: number): boolean =>
    !!selDate && !inWindow(instantOf(selDate, selHour, minute), minMs, maxMs);

  const dayClasses = (cell: MonthCell, disabled: boolean) => {
    const isSelected = isSingle
      ? cell.iso === selDate
      : cell.iso === pendingStart || cell.iso === pendingEnd;
    const inRange =
      !isSingle
      && !!pendingStart
      && !!pendingEnd
      && cell.iso > pendingStart
      && cell.iso < pendingEnd;
    const base =
      'flex h-8 w-8 items-center justify-center rounded-md text-[13px] transition-colors motion-reduce:transition-none';
    if (disabled) return `${base} cursor-not-allowed text-ink-700 opacity-40`;
    if (isSelected) return `${base} bg-agent text-white`;
    if (inRange) return `${base} bg-agent-soft text-ink-100`;
    if (!cell.inMonth) return `${base} text-ink-600 hover:bg-line`;
    return `${base} text-ink-100 hover:bg-line`;
  };

  const timeCellClasses = (active: boolean, disabled: boolean) => {
    const base =
      'w-full py-1.5 text-center text-[12px] transition-colors motion-reduce:transition-none';
    if (disabled) return `${base} cursor-not-allowed text-ink-700 opacity-40`;
    if (active) return `${base} bg-agent-soft font-medium text-ink-100`;
    return `${base} text-ink-400 hover:bg-line hover:text-ink-100`;
  };

  const segments = [
    {
      seg: 'start' as const,
      label: t('common.dateRangePopover.start', 'Start'),
      value: pendingStart,
      hint: t('common.dateRangePopover.editStart', 'Edit start date only'),
    },
    {
      seg: 'end' as const,
      label: t('common.dateRangePopover.end', 'End'),
      value: pendingEnd,
      hint: t('common.dateRangePopover.editEnd', 'Edit end date only'),
    },
  ];

  const clearLabel =
    editing === 'start'
      ? t('common.dateRangePopover.clearStart', 'Clear start')
      : editing === 'end'
        ? t('common.dateRangePopover.clearEnd', 'Clear end')
        : t('common.dateRangePopover.clear', 'Clear');

  const selectedLabel = selDate
    ? (withTime ? `${selDate} ${pad2(selHour)}:${pad2(selMinute)}` : selDate)
    : t('common.dateRangePopover.empty', '–');

  return createPortal(
    <div
      ref={ref}
      data-testid="date-time-popover"
      role="dialog"
      aria-label={
        isSingle
          ? t('common.dateTimePopover.title', 'Select date and time')
          : t('common.dateRangePopover.title', 'Select date range')
      }
      className="z-[60] rounded-xl border border-line-strong bg-island p-3 text-ink-100 shadow-2xl"
      style={{ position: 'fixed', top: 0, left: 0, width }}
    >
      {showQuick && (
        <div className="mb-2 flex gap-1" data-testid="date-time-quick">
          {quickTargets(new Date()).map((q) => {
            const disabled = !dayAllowed(q.iso, minMs, maxMs, withTime);
            return (
              <button
                key={q.key}
                type="button"
                disabled={disabled}
                onClick={() => handleDayClick(q.iso)}
                className="flex-1 truncate rounded-md border border-line px-1 py-1 text-[11px] text-ink-300 transition-colors hover:border-line-strong hover:text-ink-100 disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none"
              >
                {t(q.key, q.def)}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex">
        {/* `shrink-0`: the time columns beside this are flex items too, and a
            flex child defaults to shrinking. Without it, any width the time
            side wants past its share comes out of the calendar — which is the
            same run-on-smear failure as an undersized CALENDAR_GRID_WIDTH,
            except it only appears in `withTime` mode. */}
        <div className="shrink-0" style={{ width: CALENDAR_GRID_WIDTH }}>
          <div className="mb-2 flex items-center justify-between">
            <button
              type="button"
              disabled={prevDisabled}
              aria-label={t('common.dateRangePopover.prevMonth', 'Previous Month')}
              onClick={() => gotoMonth(-1)}
              className="flex h-6 w-6 items-center justify-center rounded text-ink-400 transition-colors hover:bg-line hover:text-ink-100 disabled:cursor-not-allowed disabled:opacity-30 motion-reduce:transition-none"
            >
              <ChevronLeft size={14} />
            </button>
            <span className="text-[13px] font-medium text-ink-100">{monthYearLabel}</span>
            <button
              type="button"
              disabled={nextDisabled}
              aria-label={t('common.dateRangePopover.nextMonth', 'Next Month')}
              onClick={() => gotoMonth(1)}
              className="flex h-6 w-6 items-center justify-center rounded text-ink-400 transition-colors hover:bg-line hover:text-ink-100 disabled:cursor-not-allowed disabled:opacity-30 motion-reduce:transition-none"
            >
              <ChevronRight size={14} />
            </button>
          </div>

          <div className="mb-1 grid grid-cols-7 gap-1">
            {WEEKDAY_KEYS.map(([key, def]) => (
              <span
                key={key}
                className="flex h-6 w-8 items-center justify-center text-[11px] text-ink-500"
              >
                {t(key, def)}
              </span>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-1">
            {cells.map((cell) => {
              const disabled =
                !dayAllowed(cell.iso, minMs, maxMs, withTime) || segmentBlocks(cell.iso);
              return (
                <button
                  key={cell.iso}
                  type="button"
                  aria-label={cell.iso}
                  disabled={disabled}
                  onClick={() => handleDayClick(cell.iso)}
                  className={dayClasses(cell, disabled)}
                >
                  {cell.date.getDate()}
                </button>
              );
            })}
          </div>
        </div>

        {withTime && (
          <div className="ml-2 flex border-l border-line pl-2" data-testid="date-time-columns">
            <div
              className="max-h-[228px] flex-1 overflow-y-auto"
              aria-label={t('common.dateTimePopover.hours', 'Hours')}
            >
              {HOURS.map((h) => (
                <button
                  key={h}
                  type="button"
                  data-testid={`date-time-hour-${pad2(h)}`}
                  aria-label={t('common.dateTimePopover.hourOption', 'Hour {{value}}', {
                    value: pad2(h),
                  })}
                  disabled={hourDisabled(h)}
                  onClick={() => handleHourClick(h)}
                  className={timeCellClasses(h === selHour, hourDisabled(h))}
                >
                  {pad2(h)}
                </button>
              ))}
            </div>
            <div
              className="max-h-[228px] flex-1 overflow-y-auto border-l border-line"
              aria-label={t('common.dateTimePopover.minutes', 'Minutes')}
            >
              {MINUTES.map((m) => (
                <button
                  key={m}
                  type="button"
                  data-testid={`date-time-minute-${pad2(m)}`}
                  aria-label={t('common.dateTimePopover.minuteOption', 'Minute {{value}}', {
                    value: pad2(m),
                  })}
                  disabled={minuteDisabled(m)}
                  onClick={() => handleMinuteClick(m)}
                  className={timeCellClasses(m === selMinute, minuteDisabled(m))}
                >
                  {pad2(m)}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {isSingle ? (
        <div className="mt-2 flex items-center gap-2 border-t border-line pt-2 text-[12px]">
          <div
            data-testid="date-time-value-cell"
            className="flex-1 truncate rounded-md border border-line px-2 py-1 text-ink-200"
          >
            {selectedLabel}
          </div>
        </div>
      ) : (
        <div className="mt-2 flex items-center gap-2 border-t border-line pt-2 text-[12px]">
          {/* Buttons, not read-outs: pressing one arms that end for a
              single-end edit (see the file header). `aria-pressed` is the whole
              state read-out — the armed cell is also highlighted, and a picker
              this small does not need a sentence explaining a highlight. */}
          {segments.map(({ seg, label, value, hint }) => {
            const active = editing === seg;
            return (
              <button
                key={seg}
                type="button"
                data-testid={`date-range-${seg}-cell`}
                aria-pressed={active}
                title={hint}
                onClick={() => toggleSegment(seg)}
                className={`flex-1 truncate rounded-md border px-2 py-1 text-left transition-colors motion-reduce:transition-none ${
                  active
                    ? 'border-agent-line bg-agent-soft text-ink-100'
                    : 'border-line text-ink-200 hover:border-line-strong hover:text-ink-100'
                }`}
              >
                <span className={`mr-1 ${active ? 'text-ink-300' : 'text-ink-500'}`}>{label}</span>
                {value ?? t('common.dateRangePopover.empty', '–')}
              </button>
            );
          })}
        </div>
      )}

      <div className="mt-2 flex items-center justify-end gap-2">
        {/* The label has to move with the armed segment: a button that says
            plain "Clear" while it would only empty one end is a button that
            lies about what it is about to do. */}
        <button
          type="button"
          data-testid="date-time-clear"
          onClick={handleClear}
          className="rounded px-2 py-1 text-[12px] text-ink-500 transition-colors hover:text-ink-200 motion-reduce:transition-none"
        >
          {clearLabel}
        </button>
        {withTime && (
          <button
            type="button"
            data-testid="date-time-done"
            onClick={onClose}
            className="rounded border border-line-strong px-2.5 py-1 text-[12px] text-ink-100 transition-colors hover:bg-line motion-reduce:transition-none"
          >
            {t('common.dateTimePopover.done', 'Done')}
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
}

export default DateTimePopover;
