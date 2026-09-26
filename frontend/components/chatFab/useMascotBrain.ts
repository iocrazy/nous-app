/**
 * useMascotBrain — what the 無我 mascot is doing, and the one-off beats.
 *
 * Continuous states are just flags that ChatFab turns into classes; the CSS
 * in index.css draws them. One-off beats (hop, landing squash, click pop,
 * ear swings) go through the Web Animations API so they can take
 * parameters and be sequenced; everything is skipped when motion is off
 * (`prefers-reduced-motion`, or a DOM without `Element.animate`).
 *
 * Idle behaviour is timer-driven: attention wears off after ATTENTION_MS,
 * the pup dozes after SLEEP_MS without any, and every 3–7s it does one
 * small thing (glance, hop, tilt, wink). Nothing here is required for the
 * button to work; it is all garnish on top of ChatFab's drag/click logic.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';

import type { FabActivity } from './fabActivity';
import type { FabSide } from './fabGeometry';

export const ATTENTION_MS = 2500;
export const SLEEP_MS = 14_000;
export const WAKE_PX = 110;
const LAND_HAPPY_MS = 1200;
const POP_MS = 260;
const ANTIC_MIN_MS = 3000;
const ANTIC_SPREAD_MS = 4000;
const FIRST_ANTIC_MS = 2500;
const EAR_MAX_DEG = 38;
/** viewBox units the open eyes may travel toward the cursor. */
const EYE_TRAVEL = 30;

export type PupFace = 'serene' | 'open' | 'happy' | 'excited' | 'working' | 'thinking' | 'asleep' | 'love';

export interface MascotBrain {
  /** Space-separated mood flags, e.g. "attentive excited". */
  mood: string;
  /** Exactly one eye group to show; CSS maps `data-face` to a `.pup-face-*`. */
  face: PupFace;
  hover: (on: boolean) => void;
  /** True while a chat state should keep the mascot visible (no peek, no sleep). */
  holdsAttention: boolean;
  touch: () => void;
  dragStart: () => void;
  dragMove: (vx: number, vy: number) => void;
  dragEnd: () => void;
  snap: (side: FabSide) => void;
  land: () => void;
  /** The click beat: wear the love face, pop, then call `done`. Synchronous when nothing can animate. */
  pop: (done: () => void) => void;
}

type Frames = Keyframe[];

export function useMascotBrain(opts: {
  rootRef: RefObject<HTMLElement | null>;
  motionOn: boolean;
  activity: FabActivity;
  unread: number;
}): MascotBrain {
  const { rootRef, motionOn, activity, unread } = opts;
  const [attentive, setAttentive] = useState(false);
  const [asleep, setAsleep] = useState(false);
  const [happy, setHappy] = useState(false);
  const [love, setLove] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [draggingState, setDraggingState] = useState(false);
  const asleepRef = useRef(false);
  asleepRef.current = asleep;
  const lastAttention = useRef(Date.now());
  const attentionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const happyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dragging = useRef(false);

  const working = activity === 'running';
  const thinking = activity === 'waiting';
  const excited = unread > 0;
  const holdsAttention = working || thinking || excited;
  const holdsRef = useRef(holdsAttention);
  holdsRef.current = holdsAttention;

  const q = useCallback(
    <T extends Element>(selector: string): T[] => {
      const root = rootRef.current;
      return root ? Array.from(root.querySelectorAll<T>(selector)) : [];
    },
    [rootRef],
  );

  const canAnimate = useCallback(
    (el: Element | undefined): el is Element & { animate: Element['animate'] } =>
      motionOn && !!el && typeof (el as Element).animate === 'function',
    [motionOn],
  );

  const anim = useCallback(
    (el: Element | undefined, frames: Frames, options: KeyframeAnimationOptions): Animation | null =>
      canAnimate(el) ? el.animate(frames, options) : null,
    [canAnimate],
  );

  const clearEyes = useCallback(() => {
    q<SVGGElement>('.pup-face-open').forEach((g) => {
      g.style.translate = '';
    });
  }, [q]);
  const clearEars = useCallback(() => {
    q<SVGElement>('.pup-ear').forEach((e) => {
      e.style.transform = '';
    });
  }, [q]);

  const attend = useCallback(() => {
    lastAttention.current = Date.now();
    setAttentive(true);
    if (attentionTimer.current) clearTimeout(attentionTimer.current);
    attentionTimer.current = setTimeout(() => {
      setAttentive(false);
      clearEyes();
    }, ATTENTION_MS);
  }, [clearEyes]);

  const earSwing = useCallback(
    (leftDeg: number, rightDeg: number, ms: number) => {
      const ears = q<SVGElement>('.pup-ear');
      const frames = (deg: number): Frames => [
        { transform: 'rotate(0)' },
        { transform: `rotate(${deg}deg)`, offset: 0.35 },
        { transform: `rotate(${-deg * 0.3}deg)`, offset: 0.7 },
        { transform: 'rotate(0)' },
      ];
      anim(ears[0], frames(leftDeg), { duration: ms, easing: 'ease-out' });
      anim(ears[1], frames(rightDeg), { duration: ms, easing: 'ease-out' });
    },
    [anim, q],
  );

  const wake = useCallback(
    (startled: boolean) => {
      lastAttention.current = Date.now();
      if (!asleepRef.current) return;
      setAsleep(false);
      if (startled) {
        attend();
        earSwing(28, -28, 520);
        anim(
          q('.chat-fab-orb')[0],
          [{ transform: 'scale(1)' }, { transform: 'scale(1.12) translateY(-4px)', offset: 0.3 }, { transform: 'scale(1)' }],
          { duration: 320, easing: 'ease-out' },
        );
      }
    },
    [anim, attend, earSwing, q],
  );

  const happyFor = useCallback((ms: number) => {
    setHappy(true);
    if (happyTimer.current) clearTimeout(happyTimer.current);
    happyTimer.current = setTimeout(() => setHappy(false), ms);
  }, []);

  const hop = useCallback(
    (height: number) => {
      anim(
        q('.chat-fab-orb')[0],
        [
          { transform: 'translateY(0) scaleX(1) scaleY(1)' },
          { transform: 'translateY(2px) scaleX(1.06) scaleY(.92)', offset: 0.15 },
          { transform: `translateY(-${height}px) scaleX(.96) scaleY(1.06)`, offset: 0.5 },
          { transform: 'translateY(0) scaleX(1.04) scaleY(.96)', offset: 0.85 },
          { transform: 'translateY(0) scaleX(1) scaleY(1)' },
        ],
        { duration: 460, easing: 'ease-in-out' },
      );
    },
    [anim, q],
  );

  // ── attention: the cursor coming near opens the eyes and wakes the pup ──
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const root = rootRef.current;
      if (!root || dragging.current) return;
      const r = root.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width / 2);
      const dy = e.clientY - (r.top + r.height / 2);
      const len = Math.hypot(dx, dy) || 1;
      if (len < WAKE_PX) {
        wake(true);
        attend();
      }
      if (!motionOn || asleepRef.current || len >= WAKE_PX) return;
      const k = Math.min(EYE_TRAVEL, (len / 50) * (EYE_TRAVEL / 2.5));
      const tx = (dx / len) * k;
      const ty = (dy / len) * k * 0.8;
      q<SVGGElement>('.pup-face-open').forEach((g) => {
        g.style.translate = `${tx.toFixed(1)}px ${ty.toFixed(1)}px`;
      });
    };
    document.addEventListener('pointermove', onMove);
    return () => document.removeEventListener('pointermove', onMove);
  }, [attend, motionOn, q, rootRef, wake]);

  // ── idle loop: doze after SLEEP_MS, otherwise a small antic now and then ──
  useEffect(() => {
    if (!motionOn) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const antics: Array<() => void> = [
      () => {
        attend();
        const faces = q<SVGGElement>('.pup-face-open');
        faces.forEach((g) => (g.style.translate = `-${EYE_TRAVEL}px 0px`));
        setTimeout(() => faces.forEach((g) => (g.style.translate = `${EYE_TRAVEL}px 0px`)), 500);
        setTimeout(() => faces.forEach((g) => (g.style.translate = '')), 1100);
      },
      () => {
        earSwing(20, -20, 460);
        hop(8);
      },
      () =>
        anim(
          q('.chat-fab-orb')[0],
          [{ transform: 'rotate(0)' }, { transform: 'rotate(-7deg)', offset: 0.3 }, { transform: 'rotate(-7deg)', offset: 0.7 }, { transform: 'rotate(0)' }],
          { duration: 1100, easing: 'ease-in-out' },
        ),
      () => {
        attend();
        const eyes = q<SVGElement>('.pup-face-open .pup-pupil');
        anim(eyes[1], [{ transform: 'scaleY(1)' }, { transform: 'scaleY(.1)', offset: 0.3 }, { transform: 'scaleY(.1)', offset: 0.7 }, { transform: 'scaleY(1)' }], {
          duration: 700,
        });
      },
    ];
    const tick = () => {
      const root = rootRef.current;
      const hovered = !!root && root.matches(':hover');
      const idleFor = Date.now() - lastAttention.current;
      if (!asleepRef.current && idleFor > SLEEP_MS && !dragging.current && !hovered && !holdsRef.current) {
        setAsleep(true);
        setAttentive(false);
        clearEyes();
        clearEars();
      } else if (!asleepRef.current && !dragging.current && !hovered && !holdsRef.current) {
        antics[Math.floor(Math.random() * antics.length)]();
      }
      timer = setTimeout(tick, ANTIC_MIN_MS + Math.random() * ANTIC_SPREAD_MS);
    };
    timer = setTimeout(tick, FIRST_ANTIC_MS);
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [anim, attend, clearEars, clearEyes, earSwing, hop, motionOn, q, rootRef]);

  // A chat state that holds attention also ends a nap.
  useEffect(() => {
    if (holdsAttention) wake(false);
  }, [holdsAttention, wake]);

  useEffect(
    () => () => {
      if (attentionTimer.current) clearTimeout(attentionTimer.current);
      if (happyTimer.current) clearTimeout(happyTimer.current);
    },
    [],
  );

  const clampDeg = (d: number) => Math.max(-EAR_MAX_DEG, Math.min(EAR_MAX_DEG, d));

  // Priority: the strongest signal wins; hover beats the chat states because
  // the user is right there, asleep is impossible while anything holds attention.
  const face: PupFace = draggingState
    ? 'open'
    : love
      ? 'love'
      : hovered || happy
        ? 'happy'
        : asleep
          ? 'asleep'
          : thinking
            ? 'thinking'
            : working
              ? 'working'
              : excited
                ? 'excited'
                : attentive
                  ? 'open'
                  : 'serene';

  const brain = useMemo<MascotBrain>(
    () => ({
      face,
      hover: (on) => {
        setHovered(on);
        if (on) {
          wake(true);
          attend();
        }
      },
      mood: [
        attentive && !asleep && 'attentive',
        happy && 'happy',
        excited && 'excited',
        working && 'working',
        thinking && 'thinking',
        love && 'love',
        asleep && 'asleep',
      ]
        .filter(Boolean)
        .join(' '),
      holdsAttention,
      touch: () => {
        wake(true);
        attend();
      },
      dragStart: () => {
        dragging.current = true;
        setDraggingState(true);
        wake(false);
        setHappy(false);
        setAttentive(false);
        clearEyes();
      },
      dragMove: (vx, vy) => {
        if (!motionOn) return;
        // t = horizontal trail (both ears same sign), s = spread from vertical motion (mirrored)
        const t = -vx * 1.6;
        const s = vy * 1.2;
        const ears = q<SVGElement>('.pup-ear');
        if (ears[0]) ears[0].style.transform = `rotate(${clampDeg(s - t)}deg)`;
        if (ears[1]) ears[1].style.transform = `rotate(${clampDeg(-s - t)}deg)`;
      },
      dragEnd: () => {
        dragging.current = false;
        setDraggingState(false);
        clearEars();
      },
      snap: (side) => {
        if (!motionOn) return;
        const dir = side === 'left' ? -1 : 1;
        q<SVGElement>('.pup-ear').forEach((e) => {
          e.style.transform = `rotate(${dir * 24}deg)`;
        });
        setTimeout(clearEars, 280);
      },
      land: () => {
        lastAttention.current = Date.now();
        anim(
          q('.chat-fab-orb')[0],
          [
            { transform: 'scaleX(1) scaleY(1)' },
            { transform: 'scaleX(1.14) scaleY(.86)', offset: 0.3 },
            { transform: 'scaleX(.96) scaleY(1.04)', offset: 0.6 },
            { transform: 'scaleX(1.02) scaleY(.98)', offset: 0.8 },
            { transform: 'scaleX(1) scaleY(1)' },
          ],
          { duration: 420, easing: 'ease-out' },
        );
        earSwing(26, -26, 520);
        happyFor(LAND_HAPPY_MS);
      },
      pop: (done) => {
        const orb = q('.chat-fab-orb')[0];
        if (!canAnimate(orb)) {
          done();
          return;
        }
        setLove(true);
        const a = orb.animate(
          [{ transform: 'scale(1)' }, { transform: 'scale(.9)', offset: 0.35 }, { transform: 'scale(1.06)', offset: 0.7 }, { transform: 'scale(1)' }],
          { duration: POP_MS, easing: 'ease-out' },
        );
        let called = false;
        const finish = () => {
          if (called) return;
          called = true;
          setLove(false);
          done();
        };
        const cap = setTimeout(finish, POP_MS + 60);
        a.finished.then(finish, finish).finally(() => clearTimeout(cap));
      },
    }),
    [anim, asleep, attend, attentive, canAnimate, clearEars, clearEyes, earSwing, excited, face, happy, happyFor, holdsAttention, love, motionOn, q, thinking, wake, working],
  );

  return brain;
}
