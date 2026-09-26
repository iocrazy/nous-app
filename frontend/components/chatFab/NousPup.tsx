/**
 * NousPup — the 無我 mascot, artwork only.
 *
 * Paths come from the approved vector (`docs/brand/nous-mascot.svg`, viewBox
 * 0 0 1024 1024, mascot group translated by 98). This file adds nothing to
 * the drawing except:
 *   - a levitation glow under the feet,
 *   - alternative eye groups for the expression sheet (the source only has
 *     the serene face),
 *   - stable class hooks so the behaviour layer (useMascotBrain and the
 *     `.chat-fab*` rules in index.css) never depends on how it is drawn:
 *
 *   .pup-ear.pup-ear-l / .pup-ear-r   the two ribbons (transform-origin at the head)
 *   .pup-curl                         the curl on top
 *   .pup-arm.pup-arm-l / .pup-arm-r   arm + hand + highlight, palms up
 *   .pup-glow                         levitation glow
 *   .pup-face.pup-face-<mood>         eye groups; CSS shows exactly one
 *   .pup-float.pup-spark / .pup-q / .pup-zz   floaters for 灵感 / 思考 / sleep
 *
 * Swapping in a newer design = replacing the paths and keeping the hooks.
 * Gradient ids carry a per-instance suffix so several pups can share a page.
 */
import React, { useId } from 'react';

/** The mascot's bounding box inside the source's 1024 canvas (after the 98px shift). */
export const PUP_VIEWBOX = '20 120 950 730';

const CURL = 'M471 102 C451 70 476 44 500 48 C521 49 540 70 536 84';
const SERENE = 'M365 298 C379 323 410 323 426 300 M493 300 C508 324 539 324 553 299';
const HAPPY = 'M365 312 C379 287 410 287 426 310 M493 310 C508 286 539 286 553 311';
const EXCITED = 'M376 278 L420 305 L376 332 M542 278 L498 305 L542 332';
const ASLEEP = 'M370 308 L422 308 M497 308 L549 308';
const INFINITY =
  'M468 526 C451 505 445 503 435 503 C406 503 406 549 435 549 C456 549 479 503 499 503 C529 503 529 549 499 549 C488 549 481 542 468 526';
const HEART_L = 'M395 332 C360 306 366 268 395 284 C424 268 430 306 395 332Z';
const HEART_R = 'M523 332 C488 306 494 268 523 284 C552 268 558 306 523 332Z';

export function NousPup(): React.ReactElement {
  const uid = useId().replace(/:/g, '');
  const u = (name: string) => `url(#${name}-${uid})`;

  return (
    <span className="chat-fab-pup" aria-hidden="true">
      <svg viewBox={PUP_VIEWBOX} focusable="false">
        <defs>
          <linearGradient id={`ear-left-${uid}`} x1="76" y1="477" x2="320" y2="258" gradientUnits="userSpaceOnUse">
            <stop stopColor="#78d2ec" />
            <stop offset=".4" stopColor="#9eb9f5" />
            <stop offset=".72" stopColor="#d0baf5" />
            <stop offset="1" stopColor="#f8e7f4" />
          </linearGradient>
          <linearGradient id={`ear-right-${uid}`} x1="704" y1="214" x2="913" y2="505" gradientUnits="userSpaceOnUse">
            <stop stopColor="#e8e6f7" />
            <stop offset=".45" stopColor="#f0bcd8" />
            <stop offset=".76" stopColor="#ffbe9c" />
            <stop offset="1" stopColor="#ffe0a5" />
          </linearGradient>
          <radialGradient id={`shell-${uid}`} cx=".31" cy=".14" r=".94">
            <stop stopColor="#ffffff" />
            <stop offset=".5" stopColor="#f8f6f6" />
            <stop offset=".83" stopColor="#e6e2ed" />
            <stop offset="1" stopColor="#e2d3d4" />
          </radialGradient>
          <radialGradient id={`body-${uid}`} cx=".3" cy=".22" r=".88">
            <stop stopColor="#ffffff" />
            <stop offset=".6" stopColor="#f6f2f2" />
            <stop offset="1" stopColor="#d7cddc" />
          </radialGradient>
          <linearGradient id={`rim-${uid}`} x1=".1" y1="0" x2=".9" y2="1">
            <stop stopColor="#e8e1e5" />
            <stop offset=".55" stopColor="#fdf8f6" />
            <stop offset="1" stopColor="#d8d1df" />
          </linearGradient>
          <radialGradient id={`visor-${uid}`} cx=".35" cy=".17" r=".91">
            <stop stopColor="#403747" />
            <stop offset=".3" stopColor="#201d2a" />
            <stop offset=".75" stopColor="#121320" />
            <stop offset="1" stopColor="#252031" />
          </radialGradient>
          <linearGradient id={`hand-${uid}`} x1="0" y1="0" x2=".7" y2="1">
            <stop stopColor="#b8a9b2" />
            <stop offset=".5" stopColor="#82778b" />
            <stop offset="1" stopColor="#514858" />
          </linearGradient>
          <linearGradient id={`core-${uid}`} x1="414" y1="512" x2="525" y2="540" gradientUnits="userSpaceOnUse">
            <stop stopColor="#df8de9" />
            <stop offset=".45" stopColor="#bc8eef" />
            <stop offset="1" stopColor="#78cbed" />
          </linearGradient>
          <linearGradient id={`reflection-${uid}`} x1="0" y1="0" x2="1" y2="1">
            <stop stopColor="#ffffff" stopOpacity=".25" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <radialGradient id={`glow-${uid}`}>
            <stop stopColor="#c08cff" stopOpacity=".5" />
            <stop offset="1" stopColor="#c08cff" stopOpacity="0" />
          </radialGradient>
        </defs>

        <g transform="translate(0 98)">
          <ellipse className="pup-glow" cx="472" cy="722" rx="170" ry="26" fill={u('glow')} />

          <path className="pup-curl" d={CURL} fill="none" stroke={u('body')} strokeWidth="28" strokeLinecap="round" />

          {/* Gradient canvas ribbons (the ears), behind the shell. */}
          <g className="pup-ear pup-ear-l">
            <path
              d="M317 194 C274 203 244 265 207 302 C156 353 90 361 62 400 C30 447 64 497 109 507 C181 527 234 482 271 426 C313 364 334 258 317 194Z"
              fill={u('ear-left')}
            />
            <path d="M78 399 C129 371 166 372 211 322 C167 393 142 417 87 420Z" fill="#fff" opacity=".17" />
          </g>
          <path
            className="pup-ear pup-ear-r"
            d="M650 190 C708 180 748 244 790 292 C845 355 898 361 926 402 C956 447 926 504 875 513 C801 531 749 486 718 426 C686 363 657 274 650 190Z"
            fill={u('ear-right')}
          />

          {/* Small body and open hands; head overlaps neck. */}
          <path
            d="M390 440 C367 471 351 524 355 573 C355 624 402 671 472 681 C541 673 596 630 595 574 C594 522 573 474 553 443Z"
            fill={u('body')}
            stroke="#d9d1df"
            strokeOpacity=".32"
            strokeWidth="2"
          />
          <g className="pup-arm pup-arm-l">
            <path
              d="M391 457 C365 469 340 487 319 505 C300 500 280 504 269 523 C258 549 274 572 300 575 C330 578 351 552 369 535Z"
              fill={u('body')}
            />
            <path
              d="M286 520 C274 514 259 508 254 502 C247 496 242 502 247 509 L256 520 C242 521 234 517 228 517 C223 514 219 519 223 527 C228 541 247 551 265 551 C278 551 286 538 286 520Z"
              fill={u('hand')}
            />
            <path d="M228 525 Q251 537 278 528" fill="none" stroke="#fff4df" strokeOpacity=".25" strokeWidth="4" strokeLinecap="round" />
          </g>
          <g className="pup-arm pup-arm-r">
            <path
              d="M552 457 C579 470 603 487 625 505 C644 500 664 504 675 523 C686 549 670 572 644 575 C614 578 593 552 575 535Z"
              fill={u('body')}
            />
            <path
              d="M658 520 C670 514 685 508 690 502 C697 496 702 502 697 509 L688 520 C702 521 710 517 716 517 C721 514 725 519 721 527 C716 541 697 551 679 551 C666 551 658 538 658 520Z"
              fill={u('hand')}
            />
            <path d="M716 525 Q693 537 666 528" fill="none" stroke="#fff4df" strokeOpacity=".25" strokeWidth="4" strokeLinecap="round" />
          </g>

          {/* Seated, floating pose. */}
          <path
            d="M393 600 C359 571 325 570 307 593 C282 623 295 664 323 681 C349 697 390 701 422 690 L446 662 C433 638 414 618 393 600Z"
            fill={u('body')}
            stroke="#ddd4e0"
            strokeOpacity=".4"
            strokeWidth="2"
          />
          <path
            d="M551 600 C585 571 619 570 637 593 C662 623 649 664 621 681 C595 697 554 701 522 690 L498 662 C511 638 530 618 551 600Z"
            fill={u('body')}
            stroke="#ddd4e0"
            strokeOpacity=".4"
            strokeWidth="2"
          />
          <path
            d="M415 656 C433 645 454 653 466 671 C480 689 468 705 449 705 C430 704 413 696 410 682 C408 673 410 663 415 656Z"
            fill={u('hand')}
          />
          <path
            d="M529 656 C511 645 490 653 478 671 C464 689 476 705 495 705 C514 704 531 696 534 682 C536 673 534 663 529 656Z"
            fill={u('hand')}
          />

          {/* Infinity: a genuine crossing curve. */}
          <path d={INFINITY} fill="none" stroke="#dec4f3" strokeOpacity=".35" strokeWidth="21" strokeLinecap="round" />
          <path className="pup-infinity" d={INFINITY} fill="none" stroke={u('core')} strokeWidth="13" strokeLinecap="round" />
          <path d="M481 511 L456 541" stroke="#fdf2ff" strokeWidth="4" opacity=".42" />

          {/* The shell and its visor form the recognizable silhouette. */}
          <path
            d="M474 99 C380 96 309 130 272 197 C247 242 249 289 255 347 C259 413 280 447 326 459 C375 472 579 474 629 460 C676 448 702 421 707 372 C713 317 703 239 677 193 C640 128 567 97 474 99Z"
            fill={u('shell')}
            stroke="#e9e2ef"
            strokeWidth="2"
          />
          <path d="M302 228 C334 155 412 125 497 128" fill="none" stroke="#fff" strokeOpacity=".7" strokeWidth="8" strokeLinecap="round" />
          <path
            d="M473 154 C389 155 325 194 305 260 C285 326 299 375 330 395 C374 423 564 424 603 396 C638 372 651 335 637 274 C622 205 563 153 473 154Z"
            fill={u('rim')}
          />
          <path
            d="M473 163 C393 164 335 201 314 264 C296 322 307 367 336 386 C376 411 559 413 597 387 C628 366 640 332 627 277 C613 212 558 162 473 163Z"
            fill={u('visor')}
            stroke="#1f1b27"
            strokeWidth="3"
          />
          <path d="M333 267 C350 212 402 183 454 180 C411 197 371 232 353 282Z" fill={u('reflection')} />
          <path d="M573 191 C604 216 619 246 623 275 C605 254 592 247 578 241Z" fill="#989bff" opacity=".075" />

          {/* Faces. Layered strokes imply glow without raster filters; CSS shows one group at a time. */}
          <g className="pup-face pup-face-serene" fill="none" strokeLinecap="round">
            <path d={SERENE} stroke="#a681ef" strokeWidth="25" opacity=".12" />
            <path d={SERENE} stroke="#ba9aff" strokeWidth="15" opacity=".34" />
            <path d={SERENE} stroke="#eee0ff" strokeWidth="8" />
          </g>
          <g className="pup-face pup-face-open">
            <rect x="377" y="264" width="36" height="76" rx="18" fill="#a681ef" opacity=".25" transform="translate(-6 -6) scale(1.03)" />
            <rect x="505" y="264" width="36" height="76" rx="18" fill="#a681ef" opacity=".25" transform="translate(-6 -6) scale(1.03)" />
            <rect className="pup-pupil" x="377" y="264" width="36" height="76" rx="18" fill="#eee0ff" />
            <rect className="pup-pupil" x="505" y="264" width="36" height="76" rx="18" fill="#eee0ff" />
          </g>
          <g className="pup-face pup-face-happy" fill="none" strokeLinecap="round">
            <path d={HAPPY} stroke="#a681ef" strokeWidth="25" opacity=".12" />
            <path d={HAPPY} stroke="#ba9aff" strokeWidth="15" opacity=".34" />
            <path d={HAPPY} stroke="#eee0ff" strokeWidth="8" />
          </g>
          <g className="pup-face pup-face-excited" fill="none" strokeLinecap="round" strokeLinejoin="round">
            <path d={EXCITED} stroke="#ba9aff" strokeWidth="22" opacity=".3" />
            <path d={EXCITED} stroke="#eee0ff" strokeWidth="11" />
          </g>
          <g className="pup-face pup-face-working">
            <rect x="379" y="290" width="32" height="28" rx="9" fill="#eee0ff" />
            <rect x="507" y="290" width="32" height="28" rx="9" fill="#eee0ff" />
          </g>
          <g className="pup-face pup-face-thinking">
            <rect x="379" y="300" width="32" height="28" rx="9" fill="#eee0ff" />
            <rect x="507" y="276" width="32" height="28" rx="9" fill="#eee0ff" />
          </g>
          <g className="pup-face pup-face-asleep" fill="none" strokeLinecap="round">
            <path d={ASLEEP} stroke="#eee0ff" strokeWidth="9" opacity=".55" />
          </g>
          <g className="pup-face pup-face-love">
            <path d={HEART_L} fill="#ff9bdc" />
            <path d={HEART_R} fill="#ff9bdc" />
          </g>
        </g>
      </svg>
      <span className="pup-float pup-spark">✦</span>
      <span className="pup-float pup-q">?</span>
      <span className="pup-float pup-zz">z</span>
      <span className="pup-float pup-zz pup-zz-2">z</span>
    </span>
  );
}
