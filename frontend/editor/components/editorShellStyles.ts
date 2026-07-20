/**
 * Scoped stylesheet for the v2 editor shell — colour + layout tokens lifted
 * verbatim from the R2-A Final mockup (ui-r2a-final.html), re-scoped so they
 * live ONLY under `.mh-editor-shell` instead of `:root` / `html.dark`. Nothing
 * here leaks into global styles: the shell renders this string in a local
 * <style> tag and the theme is selected by the `data-theme` attribute on the
 * root div, so light and dark can coexist without touching document chrome.
 *
 * Dark theme is warm-tinted ink (deep-plum chrome, deep-ink paper, dimmed
 * amber) — explicitly NOT zinc, per the island design rules.
 */
export const EDITOR_SHELL_STYLES = `
.mh-editor-shell[data-theme='light']{
  --bg:#f1f0f8; --bg-2:#e9e7f6;
  --surface:#ffffff; --surface-border:#e2dff2; --surface-2:#f6f5fc;
  --ink:#1c1830; --ink-soft:#5b5771; --ink-faint:#8d89a6;
  --indigo:#4f3ee0; --indigo-deep:#3c2ec4; --indigo-soft:#ece9fb;
  --violet:#8b30e0; --violet-soft:#f4eafd;
  --tick-action:#726e88; --tick-dialogue:var(--indigo); --tick-character:var(--violet);
  --tick-paren:#c3bdd8; --tick-transition:#b45309; --tick-comment:#0d9488; --tick-subtitle:#8d89a6;
  --sheet-bg:#ffffff; --sheet-bg-2:#fbfaff; --sheet-border:#e6e2f5;
  --sheet-ink:#211c34; --sheet-ink-soft:#6a6580;
  --green:#12945a; --green-soft:#e2f6ec; --red:#e0435a; --red-soft:#fbe6ea;
  --amber:#b45309; --amber-soft:#fdf1de;
  --shadow-island:0 1px 2px rgba(30,20,70,0.05), 0 10px 26px -10px rgba(45,30,110,0.12);
  --shadow-float:0 18px 44px -12px rgba(30,15,90,0.30), 0 3px 10px rgba(25,15,70,0.10);
  --shadow-sheet:0 1px 1px rgba(30,15,80,0.02), 0 26px 50px -22px rgba(35,20,90,0.16), 0 4px 14px rgba(35,20,90,0.05);
  --tick-glow:none; --grain-opacity:0.05; --grain-blend:multiply;
  --radius-lg:20px; --radius-md:13px; --radius-sm:9px;
  --accent-on:#ffffff;
  --minimap-mask:rgba(241,240,248,0.60);
}
.mh-editor-shell[data-theme='dark']{
  --bg:#171320; --bg-2:#1c1727;
  --surface:#221c2e; --surface-border:#362c44; --surface-2:#2a2236;
  --ink:#f0eafb; --ink-soft:#b9aed1; --ink-faint:#8a7fa1;
  --indigo:#9184f7; --indigo-deep:#b3a6ff; --indigo-soft:#2c2547;
  --violet:#cc93f7; --violet-soft:#372a49;
  --tick-action:#a199b8; --tick-dialogue:var(--indigo); --tick-character:var(--violet);
  --tick-paren:#5c5372; --tick-transition:#f2c464; --tick-comment:#2dd4bf; --tick-subtitle:#8a7fa1;
  --sheet-bg:#130f1b; --sheet-bg-2:#171223; --sheet-border:#2c2438;
  --sheet-ink:#ece5fa; --sheet-ink-soft:#9b8fb5;
  --green:#57e0a1; --green-soft:#1c3129; --red:#ff7a8a; --red-soft:#3a1f26;
  --amber:#f2c464; --amber-soft:#35291a;
  --shadow-island:0 1px 2px rgba(0,0,0,0.35), 0 10px 30px -12px rgba(0,0,0,0.55);
  --shadow-float:0 20px 50px -14px rgba(0,0,0,0.65), 0 4px 14px rgba(0,0,0,0.4);
  --shadow-sheet:0 1px 1px rgba(0,0,0,0.3), 0 30px 60px -24px rgba(0,0,0,0.7), 0 4px 16px rgba(0,0,0,0.3);
  --tick-glow:0 0 7px currentColor; --grain-opacity:0.065; --grain-blend:overlay;
  --radius-lg:20px; --radius-md:13px; --radius-sm:9px;
  --accent-on:#1a1424;
  --minimap-mask:rgba(19,15,27,0.62);
}

.mh-editor-shell{
  --mono:"JetBrains Mono","Fira Code",ui-monospace,"SF Mono","Cascadia Mono",Consolas,monospace;
  /* Screenplay typeface — Hollywood standard Courier for Latin, then a CJK
     manuscript fallback (仿宋/楷体) so mixed EN+ZH scripts both sit on the
     fixed monospace column grid. All system fonts, no web-font download. */
  --script-mono:"Courier Prime","Courier New",Courier,"STFangsong","FangSong","仿宋","STKaiti","KaiTi","SimSun","宋体",ui-monospace,monospace;
  /* Asian (华语) manuscript typefaces — labels/cues in 黑体 (bold sans CJK),
     body (action / dialogue / paren) in 宋体 (serif CJK), per the 国内分景剧本
     convention (标注/角色名用黑体, 正文用宋体). All system fonts, no download. */
  --script-hei:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Heiti SC","Noto Sans CJK SC","SimHei","黑体",sans-serif;
  --script-song:"Songti SC","STSong","SimSun","宋体","Noto Serif CJK SC","Source Han Serif SC",serif;
  --sans:-apple-system,"Inter","Segoe UI",Helvetica,Arial,sans-serif;
  /* ── Neutral-ink chrome (laper parity) ──────────────────────────────────
     The editor shell's active / selected chrome used to be a wall of brand
     indigo (active toolbar pills, doc tabs, the pagination + format
     segmenteds, selected scene rows, stat tiles, count/IE chips) — read as
     "整体蓝白". laper's chrome is ink, not colour: the active state is a near-
     black pill (light) / near-white pill (dark), selection is a soft warm-grey
     ink wash. These tokens carry that language. Every value derives from the
     theme's own --ink / --surface, so light and dark INVERT automatically
     (deep-ink pill ↔ bright-ink pill) and no new hex enters the sheet. Brand
     indigo now survives only on the focus ring and genuine primary CTAs (Save
     version, empty-state create) — the single accent, not the whole chrome. */
  --pill-ink-bg:var(--ink);                                   /* active pill: near-black (light) / near-white (dark) */
  --pill-ink-on:var(--surface);                               /* text on the pill — inverts with it */
  --sel-ink-bg:color-mix(in srgb, var(--ink) 8%, transparent);/* soft selected row / current item */
  --sel-ink-fg:var(--ink);                                    /* text on a soft-selected row */
  --hover-ink-bg:color-mix(in srgb, var(--ink) 6%, transparent);
  --chip-ink-bg:color-mix(in srgb, var(--ink) 8%, transparent);/* neutral count / IE / stat chip fill */
  --chip-ink-fg:var(--ink-soft);                              /* quiet ink for chip text */
  --emph-ink-border:color-mix(in srgb, var(--ink) 42%, var(--surface-border));
  --emph-ink-glow:color-mix(in srgb, var(--ink) 12%, transparent);
  /* Asian (华语) per-element indent grid — laper 亚洲格式 parity. Every value is a
     named ch var so the whole hierarchy scales 1:1 with display zoom (which scales
     the row font-size, and ch tracks font-size). action/dialogue share a 4ch body
     column so they read as one indented text block; the △ / ∟ manuscript marks
     hang in the gutter to the left of that column. */
  --as-action-mark:1ch;        /* △ hangs here (left of the action body column) */
  --as-action-body:4ch;        /* action text column + hanging indent for wraps */
  --as-dialogue-body:4ch;      /* dialogue text column (aligned with action body) */
  --as-dialogue-mark:1.5ch;    /* ∟ continuation mark hangs here on wrapped dialogue */
  --as-comment-bar:0.5ch;      /* comment quote bar offset from the text edge */
  --as-comment-body:4ch;       /* comment text column */
  --as-paren-body:0.5ch;       /* parenthetical indent */
  position:absolute; inset:0;
  background:radial-gradient(circle at 15% 0%, var(--bg-2) 0%, var(--bg) 55%);
  color:var(--ink);
  font-family:var(--sans);
  display:grid;
  /* minmax(0,1fr) — NOT a bare 1fr — for the script column: a 1fr track's
     implicit minimum is auto, which is the min-CONTENT of the fixed-width
     .mh-sheet (780px). At narrow widths (embedded in the workspace, or a small
     window) that floors the middle track at ~780px, so the column overflows the
     shell's right edge and overflow:hidden clips the paper's right side. The
     minmax(0,...) lets the track shrink below the sheet's width so .mh-sheet's
     max-width:100% can fit the paper to the window (image-90 report). */
  grid-template-columns:auto minmax(0, 1fr) auto;
  gap:16px;
  padding:16px;
  overflow:hidden;
}
.mh-editor-shell *{ box-sizing:border-box; }
/* Embedded in the projects workspace: NO boxed left rail — the workspace tree
   owns episode + module navigation, and scene navigation is the floating
   SceneToc overlaying the paper (identical to the standalone route). So the
   shell reflows to two tracks: script column + Writing panel. The script
   column keeps its minmax(0,1fr) + .mh-sheet{min-width} floor so the paper
   scrolls (never clips) at narrow widths — narrow-width spec guarantee. */
.mh-editor-shell.mh-embedded{ grid-template-columns:minmax(0, 1fr) auto; }
/* Embedded: don't paint the standalone lavender page gradient — it clashed with
   the workspace's own surface (a mismatched purple block in the empty area
   right of the sheet). Go transparent so the workspace background shows through
   and the whole area reads as ONE colour. The sheet keeps its own paper bg +
   shadow, so it still reads as a page floating on the workspace surface. */
.mh-editor-shell.mh-embedded{ background:transparent; }

.mh-island{
  background:var(--surface);
  border:1px solid var(--surface-border);
  border-radius:var(--radius-lg);
  box-shadow:var(--shadow-island);
}

/* ===== LEFT RAIL ===== */
.mh-rail{ width:258px; display:flex; flex-direction:column; min-height:0; transition:width 0.2s ease; }
.mh-rail.collapsed{ width:56px; }
.mh-rail-top{ padding:16px 14px 12px; }
.mh-rail-collapsed-strip{ padding:14px 0; display:flex; flex-direction:column; align-items:center; gap:14px; }
.mh-brand-row{
  display:flex; align-items:center; gap:8px;
  font-size:11.5px; font-weight:700; letter-spacing:0.07em;
  color:var(--ink-faint); text-transform:uppercase; margin-bottom:12px;
}
.mh-brand-dot{
  width:9px; height:9px; border-radius:3px;
  background:linear-gradient(135deg,var(--indigo),var(--violet));
  transform:rotate(45deg); flex-shrink:0;
}
.mh-ep-selector-wrap{ position:relative; }
.mh-ep-selector{
  display:block; width:100%; text-align:left; cursor:pointer; font-family:var(--sans);
  background:var(--surface-2); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); padding:10px 12px;
}
.mh-ep-selector:hover{ border-color:color-mix(in srgb, var(--ink) 28%, var(--surface-border)); }
.mh-ep-selector:focus-visible{ outline:2px solid var(--indigo); outline-offset:2px; }
.mh-ep-name{ font-weight:700; font-size:14px; color:var(--ink); }
.mh-ep-sub{ font-size:11px; color:var(--ink-faint); margin-top:2px; }

/* ===== EPISODE PANEL (multi-episode management, Task 5) ===== */
.mh-ep-panel{
  margin-top:8px; padding:8px; display:flex; flex-direction:column; gap:4px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); box-shadow:var(--shadow-island);
}
.mh-ep-panel-head{ display:flex; align-items:center; gap:8px; padding:2px 4px 6px; }
.mh-ep-panel-head .mh-rail-section-label{ margin-right:auto; }
.mh-ep-new-btn{
  font-family:var(--sans); font-size:11.5px; font-weight:600;
  padding:4px 10px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface-2); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-ep-new-btn:hover:not(:disabled){ background:var(--hover-ink-bg); color:var(--ink); }
.mh-ep-new-btn:disabled{ opacity:0.5; cursor:default; }
.mh-ep-list{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px; }
.mh-ep-item{ display:flex; align-items:center; gap:4px; }
.mh-ep-item-main{
  flex:1; min-width:0; display:flex; align-items:center; gap:8px;
  padding:7px 9px; border-radius:var(--radius-sm); cursor:pointer;
  background:none; border:1px solid transparent; text-align:left; font-family:var(--sans);
  color:var(--ink-soft);
}
.mh-ep-item-main:hover:not(:disabled){ background:var(--surface-2); }
.mh-ep-item-main.current{ background:var(--sel-ink-bg); color:var(--sel-ink-fg); cursor:default; }
.mh-ep-item-title{
  flex:1; min-width:0; font-size:12.5px; font-weight:600;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-ep-item-count{
  flex-shrink:0; font-family:var(--mono); font-size:10.5px; color:var(--ink-faint);
}
.mh-ep-current-badge{
  flex-shrink:0; font-size:9.5px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase;
  padding:1px 6px; border-radius:8px; background:var(--pill-ink-bg); color:var(--pill-ink-on);
}
.mh-ep-delete-btn{
  flex-shrink:0; width:24px; height:24px; border-radius:var(--radius-sm);
  display:flex; align-items:center; justify-content:center; cursor:pointer;
  background:none; border:1px solid transparent; color:var(--ink-faint); font-size:15px;
}
.mh-ep-delete-btn:hover:not(:disabled){ background:var(--surface-2); color:var(--red); }
.mh-ep-delete-btn:disabled{ opacity:0.35; cursor:not-allowed; }
.mh-ep-rename-input{
  flex:1; min-width:0; font-family:var(--sans); font-size:12.5px; font-weight:600;
  padding:6px 9px; border-radius:var(--radius-sm);
  border:1px solid var(--indigo); background:var(--surface); color:var(--ink);
}
/* Section header row: uppercase label + a right-aligned count badge (laper
   "Assets ②" pattern). One shared style so every rail region reads the same. */
.mh-rail-section-head{
  display:flex; align-items:center; gap:8px; padding:10px 16px 5px;
}
.mh-rail-section-label{
  font-size:10.5px; font-weight:700; letter-spacing:0.06em; text-transform:uppercase;
  color:var(--ink-faint);
}
.mh-rail-count-badge{
  margin-left:auto; flex-shrink:0;
  min-width:17px; height:17px; padding:0 5px; border-radius:9px;
  display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--mono); font-size:10px; font-weight:700; line-height:1;
  background:var(--chip-ink-bg); color:var(--chip-ink-fg);
}
.mh-scene-list{ flex:1; overflow-y:auto; padding:4px 10px 14px; display:flex; flex-direction:column; gap:5px; }
.mh-scene-row{
  display:flex; align-items:flex-start; gap:9px; padding:9px;
  border-radius:var(--radius-sm); cursor:pointer;
  border:1px solid transparent; text-align:left; background:none; width:100%;
  font-family:var(--sans);
}
.mh-scene-row:hover{ background:var(--surface-2); }
.mh-scene-row.active{ background:var(--sel-ink-bg); border-color:var(--surface-border); }
.mh-scene-num-chip{
  width:21px; height:21px; border-radius:6px; background:var(--ink-faint);
  color:var(--surface); font-size:10.5px; font-weight:700; font-family:var(--mono);
  display:flex; align-items:center; justify-content:center; flex-shrink:0; margin-top:1px;
}
.mh-scene-row.active .mh-scene-num-chip{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-scene-meta-text{ min-width:0; flex:1; }
.mh-scene-row-head{ display:flex; align-items:center; gap:6px; }
.mh-ie-badge{
  font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:0.03em;
  padding:1px 5px; border-radius:4px; flex-shrink:0;
}
/* INT / EXT read as one neutral ink chip — the INT/EXT text carries the
   distinction, not two brand colours (laper keeps sluglines monochrome). */
.mh-ie-badge.int{ background:var(--chip-ink-bg); color:var(--chip-ink-fg); }
.mh-ie-badge.ext{ background:var(--chip-ink-bg); color:var(--chip-ink-fg); }
.mh-scene-title{
  font-size:12px; font-weight:600; color:var(--ink);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-scene-slug{
  font-size:10.5px; color:var(--ink-faint); margin-top:3px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}

/* ===== RAIL INFO ARCHITECTURE (modules nav + Characters/Locations) ===== */
/* Regions read as a clear stack (laper): the modules nav is fenced off from the
   entity/scene sections below it with a hairline divider. */
.mh-rail-modules{
  padding:2px 10px 8px; margin-bottom:2px; display:flex; flex-direction:column; gap:2px;
  border-bottom:1px solid var(--surface-border);
}
.mh-rail-module{
  display:flex; align-items:center; gap:9px; width:100%; text-align:left;
  padding:8px 9px; border-radius:var(--radius-sm);
  font-family:var(--sans); font-size:12.5px; font-weight:600;
  color:var(--ink-soft); background:none; border:1px solid transparent; cursor:pointer;
}
.mh-rail-module:hover:not(:disabled){ background:var(--surface-2); }
.mh-rail-module:disabled{ opacity:0.5; cursor:default; }
.mh-rail-module.active{ background:var(--sel-ink-bg); color:var(--sel-ink-fg); border-color:var(--surface-border); }
.mh-rail-module.active:hover:not(:disabled){ background:var(--sel-ink-bg); }
.mh-rail-module-glyph{
  width:16px; text-align:center; flex-shrink:0;
  font-family:var(--mono); font-size:12px; color:var(--ink-faint);
}
.mh-rail-module.active .mh-rail-module-glyph{ color:var(--sel-ink-fg); }
/* One scroll region for Characters + Locations + Scenes; the modules nav and
   Episode selector above it stay fixed (navigation never scrolls away). */
.mh-rail-scroll{ flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; }
.mh-rail-scroll .mh-scene-list{ flex:0 0 auto; overflow:visible; }
.mh-rail-section{ display:flex; flex-direction:column; }
/* Stacked sections (Characters → Locations → Scenes) each carry a hairline top
   divider so the hierarchy reads even when a section runs long. */
.mh-rail-scroll > .mh-rail-section + .mh-rail-section > .mh-rail-section-head{
  border-top:1px solid var(--surface-border); margin-top:2px;
}
/* Entity rows sit one indent level deeper than their section label. */
.mh-rail-entity-list{ padding:0 10px 8px 14px; display:flex; flex-direction:column; gap:1px; }
.mh-rail-entity-row{
  display:flex; align-items:center; gap:8px; width:100%; text-align:left;
  padding:6px 9px; border-radius:var(--radius-sm);
  font-family:var(--sans); color:var(--ink-soft); background:none; border:none; cursor:pointer;
}
.mh-rail-entity-row:hover{ background:var(--surface-2); }
.mh-rail-entity-name{
  flex:1; min-width:0; font-size:12.5px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-rail-entity-count{ flex-shrink:0; font-size:10.5px; color:var(--ink-faint); font-family:var(--mono); }
.mh-rail-empty{ padding:2px 16px 8px; font-size:11.5px; color:var(--ink-faint); }

/* ===== ICON BUTTONS (collapse toggles, theme toggle) ===== */
.mh-icon-btn{
  display:flex; align-items:center; justify-content:center;
  width:30px; height:30px; border-radius:9px;
  background:var(--surface-2); border:1px solid var(--surface-border);
  color:var(--ink-soft); cursor:pointer; font-size:13px; flex-shrink:0;
  font-family:var(--sans);
}
.mh-icon-btn:hover{ background:var(--hover-ink-bg); color:var(--ink); }

/* ===== CENTER COLUMN ===== */
.mh-center-col{ display:flex; flex-direction:column; min-height:0; position:relative; }
.mh-center-topbar{ display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; flex-shrink:0; gap:12px; }
.mh-doc-tabs{
  display:flex; gap:4px; background:var(--surface);
  border:1px solid var(--surface-border); border-radius:12px; padding:4px;
}
.mh-doc-tab{
  font-size:12.5px; font-weight:600; padding:7px 16px; border-radius:9px;
  color:var(--ink-soft); cursor:pointer; background:none; border:none; font-family:var(--sans);
}
.mh-doc-tab[aria-selected='true']{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-topbar-right{ display:flex; align-items:center; gap:10px; }

/* ── Collaboration presence (Phase B P5 / C1) ─────────────────────────────── */
.mh-presence-avatars{ display:flex; align-items:center; }
.mh-presence-avatar{
  display:inline-flex; align-items:center; justify-content:center;
  width:26px; height:26px; margin-left:-7px; border-radius:50%;
  background:var(--surface-2); color:var(--ink-soft);
  border:2px solid var(--surface); box-shadow:var(--shadow-island);
  font-size:11px; font-weight:600; font-family:var(--sans);
  user-select:none;
}
.mh-presence-avatar:first-child{ margin-left:0; }
.mh-presence-avatar.editing{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-presence-avatar.overflow{ background:var(--surface-2); color:var(--ink-soft); }

.mh-scene-presence-badge{
  display:inline-flex; align-items:center; gap:4px;
  padding:1px 8px; border-radius:999px;
  background:var(--surface-2); color:var(--ink-soft);
  border:1px solid var(--surface-border);
  font-size:11px; font-weight:500; font-family:var(--sans);
  white-space:nowrap;
}
.mh-scene-presence-badge.editing{
  background:var(--sel-ink-bg); color:var(--sel-ink-fg); border-color:transparent;
}
.mh-flow-scene-head .mh-scene-presence-badge{ margin-left:auto; }

.mh-page-frame{ flex:1; min-height:0; display:flex; position:relative; }

/* ===== SCENE TOC (Notion-style floating table of contents) =====
   A minimal tick rail pinned to the LEFT edge of the paper frame that floats a
   full scene panel on hover / pin. It overlays the paper (position:absolute) so
   it NEVER pushes the sheet — the fix for "又是一个框". Ink language matches the
   islands (#1430/#1438): surface panel, float shadow, warm-grey ticks. */
.mh-scene-toc{
  /* Above the sticky Element Toolbar (z-index:40) so the floating panel wins the
     hit-test where they overlap at the sheet's left edge. */
  position:absolute; left:0; top:0; bottom:0; z-index:41;
  /* Centred vertically on the paper's left edge (Notion parity) — this also
     clears the sticky Element Toolbar that sits at the top. */
  display:flex; align-items:center;
  /* Container is click-through; only the ticks + panel opt back into pointer
     events, so the empty space over the paper stays fully interactive. */
  pointer-events:none;
}
/* Tick rail — one short line per scene; the current scene's line is longer +
   inked. Scrolls when a long script overflows the frame height (min gap keeps
   the lines from fusing into a smear). */
.mh-toc-ticks{
  pointer-events:auto;
  display:flex; flex-direction:column; gap:7px;
  padding:20px 12px 20px 6px;
  max-height:100%; overflow-y:auto; overflow-x:hidden;
  transition:opacity 0.16s ease;
}
.mh-toc-ticks::-webkit-scrollbar{ width:0; height:0; }
.mh-toc-tick{
  flex-shrink:0;
  width:15px; height:2px; padding:0; border:none; border-radius:2px;
  background:var(--ink-faint); opacity:0.45; cursor:pointer;
  transition:width 0.12s ease, opacity 0.12s ease, background 0.12s ease;
}
.mh-toc-tick:hover{ width:22px; opacity:0.8; }
.mh-toc-tick.active{ width:28px; opacity:1; background:var(--ink); }
/* Floating panel — reuses the SceneRail list. Hidden (faded + shifted) until the
   toc is open (hover/pin) OR a scene row inside it gains focus (keyboard reveal).
   Kept in the DOM so screen readers + Tab can always reach the list. */
.mh-toc-panel{
  position:absolute; left:0; top:50%;
  width:244px; max-height:100%;
  display:flex; flex-direction:column; overflow:hidden;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-lg); box-shadow:var(--shadow-float);
  opacity:0; transform:translate(-8px, -50%); pointer-events:none;
  transition:opacity 0.16s ease, transform 0.16s ease;
}
.mh-scene-toc[data-open='true'] .mh-toc-panel,
.mh-scene-toc:focus-within .mh-toc-panel{
  opacity:1; transform:translate(0, -50%); pointer-events:auto;
}
/* When the panel is up, fade the ticks out (the panel replaces them in place). */
.mh-scene-toc[data-open='true'] .mh-toc-ticks,
.mh-scene-toc:focus-within .mh-toc-ticks{ opacity:0; pointer-events:none; }
.mh-toc-panel-head{
  display:flex; align-items:center; gap:8px; flex-shrink:0;
  padding:12px 12px 8px;
}
.mh-toc-panel-title{
  font-size:10.5px; font-weight:700; letter-spacing:0.06em; text-transform:uppercase;
  color:var(--ink-faint);
}
.mh-toc-panel-count{
  flex-shrink:0;
  min-width:17px; height:17px; padding:0 5px; border-radius:9px;
  display:inline-flex; align-items:center; justify-content:center;
  font-family:var(--mono); font-size:10px; font-weight:700; line-height:1;
  background:var(--chip-ink-bg); color:var(--chip-ink-fg);
}
.mh-toc-pin{ margin-left:auto; width:26px; height:26px; font-size:13px; }
.mh-toc-pin.pinned{ background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent; }
.mh-toc-pin.pinned:hover{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-toc-panel-body{ flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; }
/* SceneRail inside the panel: let the BODY own the scroll (its own flex:1 +
   overflow would double-scroll), and tighten the padding to the panel edges. */
.mh-toc-panel-body .mh-scene-list{ flex:0 0 auto; overflow:visible; padding:2px 8px 10px; }
.mh-sheet-scroll{
  flex:1; min-height:0; min-width:0; overflow-y:auto;
  display:flex; flex-direction:column; align-items:center; gap:16px;
  /* laper parity: the script is a SHEET OF PAPER, not a UI card. Drop the
     surrounding gray-lavender card (bg + border + radius) so the paper sits
     cleanly on the workspace background — the paper keeps its own border +
     shadow (.mh-sheet) so it still reads as a sheet. */
  background:transparent; border:none; border-radius:0;
  padding:20px 26px 22px;
}
.mh-sheet{
  /* Industry screenplay margins (laper parity), expressed in the Courier
     character grid so they line up with the fixed 60ch text column below:
       --sheet-pad-l 15ch = 1.5" left margin  (10 cpi → 15 chars)
       --sheet-pad-r 10ch = 1"   right margin (10 cpi → 10 chars)
     Every consumer that must reach the paper edge (the page-seam bleed math)
     derives its offset from these + the border width, never a raw magic px. */
  --sheet-pad-l:15ch; --sheet-pad-r:10ch; --sheet-border-w:1px;
  /* 85ch frame = 15 (left) + 60 (text) + 10 (right); + both borders. With
     box-sizing:border-box the CONTENT box lands at exactly 60ch, so the
     left-aligned .hw-* text column fills it and the leftover width can no
     longer pile up on the right (the asymmetric right-whitespace report). */
  width:calc(60ch + var(--sheet-pad-l) + var(--sheet-pad-r) + 2 * var(--sheet-border-w)); max-width:100%;
  /* Graceful floor for very tight widths: the Hollywood ch-indents
     (.hw-dialogue 10ch+15ch) are FIXED padding that never shrinks, so squashing
     the paper too far would render dialogue one character per line. Below the
     floor the paper overflows .mh-sheet-scroll horizontally instead
     (overflow-y:auto makes its overflow-x auto too), giving a scrollbar — never
     squashed, never clipped. margin-inline:auto (not the parent's
     align-items:center) does the centering: auto margins collapse to 0 when the
     paper is wider than the scroller, keeping the LEFT edge reachable by scroll
     (a centered overflow flex item's left side is unreachable — classic
     data-loss trap).
     Floor raised 480 → 520: the industry margins (25ch of sheet padding) plus
     dialogue's own 25ch indent eat 50ch of the width, so at 480 the dialogue
     measure fell to ~69px (one short word per line); 520 keeps a readable
     >100px column — the narrow-width spec's guarantee. */
  min-width:520px; margin-inline:auto;
  background:linear-gradient(180deg, var(--sheet-bg) 0%, var(--sheet-bg-2) 100%);
  border:1px solid var(--sheet-border); border-radius:6px;
  box-shadow:var(--shadow-sheet); padding:34px var(--sheet-pad-r) 44px var(--sheet-pad-l);
  /* The paper shares the SCRIPT's Courier grid (--script-mono @ 13.5px, the
     same font-size the .hw-* text column resolves 60ch against) so the ch-based
     margins here — and the seam-bleed offsets derived from them — resolve to
     the identical px as the text column. */
  font-family:var(--script-mono); font-size:13.5px; color:var(--sheet-ink);
  position:relative; min-height:60%; isolation:isolate;
  /* Column-flex item in .mh-sheet-scroll: the default flex-shrink:1 lets the
     bounded scroll container squash the paper back to its min-height, turning
     that floor into a ceiling and painting the rest of the script outside the
     sheet (which is overflow:visible). The paper must never shrink. */
  flex-shrink:0;
}
.mh-sheet::after{
  content:""; position:absolute; inset:0; border-radius:6px;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix type='matrix' values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.7 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");
  background-size:180px 180px;
  opacity:var(--grain-opacity); mix-blend-mode:var(--grain-blend);
  pointer-events:none; z-index:0;
}
.mh-sheet-inner{ position:relative; z-index:1; }
.mh-scene-block{ margin-bottom:26px; position:relative; padding-left:4px; }
/* A5: the scene-row drag handle is the SAME 6-dot affordance as the per-element
   '.mh-el-drag' handle (see ELEMENT ROWS below) — one handle style across the
   whole document, not a bespoke "::" glyph. Kept as its own class (positioned
   directly on the scene row rather than inside a shared gutter box) but every
   visual property mirrors '.mh-el-drag' exactly. */
.mh-drag-handle{
  position:absolute; left:-16px; top:1px;
  display:grid; grid-template-columns:repeat(2, 3px); grid-template-rows:repeat(3, 3px);
  gap:2px; padding:3px; border:none; background:none; border-radius:4px;
  color:var(--sheet-ink-soft); cursor:grab; line-height:0;
  user-select:none; opacity:0; transition:opacity 0.12s ease;
}
.mh-drag-handle:hover{ color:var(--sheet-ink); background:var(--surface-2); }
.mh-drag-handle:active,
.mh-drag-handle.dragging{ cursor:grabbing; }
.mh-drag-handle:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
.mh-scene-block:hover .mh-drag-handle,
.mh-scene-block:focus-within .mh-drag-handle{ opacity:1; }
.mh-scene-headrow{ display:flex; align-items:center; gap:8px; margin-bottom:13px; flex-wrap:wrap; }
/* laper parity: each heading token is a dropdown trigger that inherits the
   .mh-scene-heading slug typography (--script-mono / 13.5px / weight 400 /
   uppercase), so opening a token NEVER resizes the heading. A FILLED token
   renders as plain slug text (no pill); only an UNSET token gets the subtle
   chip tint — so a partially-filled heading still reads as a proper slug. */
.mh-scene-select{
  font-family:var(--script-mono); font-size:13.5px; font-weight:400; letter-spacing:0.01em;
  text-transform:uppercase;
  padding:0 3px; border-radius:4px; border:1px solid transparent;
  background:transparent;
  color:var(--sheet-ink); cursor:pointer; white-space:nowrap; line-height:1.5;
}
/* Unset token = laper's subtle chip. */
.mh-scene-select[data-placeholder]{
  padding:0 7px;
  background:color-mix(in srgb, var(--sheet-ink) 7%, transparent);
  color:var(--sheet-ink-soft);
}
.mh-scene-select:hover,
.mh-scene-select[aria-expanded='true']{
  background:color-mix(in srgb, var(--sheet-ink) 12%, transparent);
}
.mh-scene-select:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
/* Asian layout keeps its own script face + case, matching .mh-scene-heading.asian. */
.mh-scene-headrow.asian .mh-scene-select{
  font-family:var(--script-hei); font-size:14px; letter-spacing:0.02em; text-transform:none;
}
/* HeadingSelect: a chip trigger over a LIGHT popup (the .mh-mention-pop DNA) so
   the INT/EXT + time menus size to their OWN content and never clip labels the
   way UiSelect's trigger-width menu did on the short heading triggers. */
.mh-heading-select{ position:relative; display:inline-flex; }
.mh-heading-pop{
  position:absolute; top:calc(100% + 3px); left:0; z-index:20;
  min-width:150px; max-width:280px;
  /* Elevated chrome surface to match .mh-mention-pop / .mh-slash-menu exactly —
     one paper-toned DNA for every editor popup, flipping with the theme so it
     never flashes white on dark paper. */
  background:var(--surface); border:1px solid var(--sheet-border);
  border-radius:10px; box-shadow:0 10px 28px rgba(35,20,90,0.14);
  padding:4px; font-family:var(--sans);
}
.mh-heading-opt{
  padding:8px 12px; border-radius:6px; font-size:13px;
  font-family:var(--mono); font-weight:700; letter-spacing:0.03em;
  color:var(--sheet-ink); cursor:pointer; white-space:nowrap;
  transition:background 0.12s ease;
}
/* laper neutrality: hover + keyboard-active are warm ink-mix greys (derived from
   --sheet-ink, so they stay neutral and theme-aware), NOT the brand indigo tint.
   Selected reads as darker-grey-on-paper with the sheet's own ink, never indigo. */
.mh-heading-opt:hover{ background:color-mix(in srgb, var(--sheet-ink) 6%, transparent); }
.mh-heading-opt.active{ background:color-mix(in srgb, var(--sheet-ink) 11%, transparent); color:var(--sheet-ink); }
.mh-heading-opt[aria-selected='true']{ font-weight:800; }
/* Searchable location picker: a chromeless search-or-create input at the top of
   the popup (mirrors the cue picker's mh-mention-search). */
.mh-heading-search{
  margin:-4px -4px 4px; padding:8px 11px;
  border-bottom:1px solid var(--sheet-border);
}
.mh-heading-search input{
  width:100%; background:none; border:none; outline:none;
  font-family:var(--sans); font-size:13px; color:var(--sheet-ink); padding:0;
}
.mh-heading-search input::placeholder{ color:var(--sheet-ink-soft); }
/* The Create row reads as an action, not a location — sans + a touch heavier,
   but the sheet's own ink (no brand indigo; the font shift carries the meaning). */
.mh-heading-opt.create{ font-family:var(--sans); font-weight:600; color:var(--sheet-ink); }
.mh-heading-empty{ padding:8px 12px; font-size:12.5px; color:var(--sheet-ink-soft); }
/* laper's "Tab — Switch to location" hint at the top of the open picker. */
.mh-heading-hint{
  display:flex; align-items:center; gap:7px; margin:0 2px 4px;
  padding:5px 8px; border-bottom:1px solid var(--sheet-border);
  font-family:var(--sans); font-size:11.5px; color:var(--sheet-ink-soft);
  white-space:nowrap;
}
/* Location input: flat chip to match .mh-scene-select, not a bordered box. */
.mh-scene-loc-input{
  font-family:var(--mono); font-size:12px; font-weight:700; letter-spacing:0.03em;
  padding:2px 8px; border-radius:4px; border:1px solid transparent;
  background:color-mix(in srgb, var(--sheet-ink) 7%, transparent);
  color:var(--sheet-ink); min-width:130px;
}
.mh-scene-loc-input::placeholder{ color:var(--sheet-ink-soft); }
.mh-scene-loc-input:hover{ background:color-mix(in srgb, var(--sheet-ink) 12%, transparent); }
.mh-scene-loc-input:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
/* Scene heading (laper parity): a row of inline token dropdowns joined by static
   .·- separators — NOT a read/edit mode swap. The container carries the slug
   typography (script Courier, uppercase); the tokens (.mh-scene-select) and the
   separators inherit it, so clicking a token opens its dropdown WITHOUT changing
   the heading's size or position. */
.mh-scene-heading{
  flex:1; min-width:0;
  font-family:var(--script-mono); font-size:13.5px; font-weight:400; letter-spacing:0.01em;
  text-transform:uppercase; color:var(--sheet-ink); line-height:1.5;
}
.mh-scene-heading.asian{
  font-family:var(--script-hei); font-size:14px; letter-spacing:0.02em; text-transform:none;
}
/* Asian scene head = "N. 地点 时间 / INT" — the scene number is an INLINE, always
   -visible BOLD prefix at the head of the slug (laper 亚洲格式), not the hollywood
   hover-reveal margin badge. Hide that margin number in asian mode (the drag
   handle in the same gutter still hover-reveals) so the number never doubles. */
.mh-scene-num-inline{
  font-family:var(--script-hei); font-weight:700; color:var(--sheet-ink);
  margin-right:2px; user-select:none;
}
.mh-scene-headrow.asian .mh-scene-num-badge{ display:none; }
/* Decorative separators between tokens — keep their exact spaces (". " / " - " /
   " · "), never interactive, slightly muted so the tokens read as the content. */
.mh-heading-sep{ white-space:pre; color:var(--sheet-ink-soft); user-select:none; }

/* ===== ELEMENT ROWS (Hollywood layout engine) ===== */
.mh-el-row{ display:flex; align-items:flex-start; gap:9px; margin:0 0 7px; position:relative; }
/* ── laper/Notion-style hover gutter: block number + 6-dot drag handle ──
   Absolutely positioned in the sheet's left margin so it NEVER participates in
   the flex row (the Hollywood ch-column grid stays exact). Hidden by default;
   revealed on row hover / focus. */
.mh-el-gutter{
  position:absolute; left:-46px; top:1px; height:1.7em;
  display:flex; align-items:center; justify-content:flex-end; gap:4px;
  width:40px; opacity:0; transition:opacity 0.12s ease;
  user-select:none;
}
/* No pointer-events:none on the base: the gutter sits in negative-x space
   outside the row's box with a small dead-zone gap, so once hover dropped there
   a pointer-events:none gutter could never be re-hovered to bring itself back
   (the bug). Keeping it hit-testable + revealing on its OWN hover lets it
   self-heal, exactly like the working .mh-scene-gutter. */
.mh-el-row:hover .mh-el-gutter,
.mh-el-row:focus-within .mh-el-gutter,
.mh-el-gutter:hover{ opacity:1; }
.mh-el-num{
  font-family:var(--mono); font-size:10.5px; font-weight:600;
  font-variant-numeric:tabular-nums; color:var(--sheet-ink-soft);
  min-width:1.2em; text-align:right;
}
.mh-el-drag{
  display:grid; grid-template-columns:repeat(2, 3px); grid-template-rows:repeat(3, 3px);
  gap:2px; padding:3px; border:none; background:none; border-radius:4px;
  color:var(--sheet-ink-soft); cursor:grab; line-height:0;
}
.mh-el-drag:hover{ color:var(--sheet-ink); background:var(--surface-2); }
.mh-el-drag:active,
.mh-el-drag.dragging{ cursor:grabbing; }
.mh-el-drag:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
.mh-el-dot{ width:3px; height:3px; border-radius:50%; background:currentColor; }
/* Drop-edge indicator: a thin indigo line on the target row's leading/trailing
   edge showing where the dragged element will land. */
.mh-el-row.drop-top::before,
.mh-el-row.drop-bottom::after{
  content:""; position:absolute; left:0; right:0; height:2px;
  background:var(--indigo); border-radius:2px; pointer-events:none;
}
.mh-el-row.drop-top::before{ top:-4px; }
.mh-el-row.drop-bottom::after{ bottom:-4px; }
.mh-el-tick{ width:3px; border-radius:2px; flex-shrink:0; align-self:stretch; min-height:18px; margin-top:3px; }
.mh-el-tick.t-action{ background:var(--tick-action); }
.mh-el-tick.t-dialogue{ background:var(--tick-dialogue); box-shadow:var(--tick-glow); }
.mh-el-tick.t-character{ background:var(--tick-character); box-shadow:var(--tick-glow); }
.mh-el-tick.t-paren{ background:var(--tick-paren); }
.mh-el-tick.t-transition{ background:var(--tick-transition); }
.mh-el-tick.t-comment{ background:var(--tick-comment); }
.mh-el-tick.t-subtitle{ background:var(--tick-subtitle); }
.mh-el-editable{
  position:relative; /* anchor for the absolute ::before placeholder overlay */
  font-size:13.5px; line-height:1.7; flex:1; outline:none;
  font-family:var(--mono); color:var(--sheet-ink);
  white-space:pre-wrap; word-break:break-word; min-height:1.7em;
}
/* Placeholder OVERLAYS the first line (absolute) so it reads inline with the
   caret and vanishes on the first keystroke — instead of sitting on its own line
   ABOVE the box. @tiptap/react's NodeViewContent wraps the real content in a
   block <div>, so an inline ::before would be pushed to a line of its own; the
   absolute overlay sidesteps that. pointer-events:none so clicks reach the caret. */
.mh-el-editable:empty::before,
.mh-el-editable:has(br.ProseMirror-trailingBreak)::before{
  content:attr(data-placeholder); color:var(--sheet-ink-soft); font-style:italic;
  position:absolute; left:0; top:0; pointer-events:none;
  /* The asian cue shrink-wraps, so an EMPTY one is ~0 wide and the overlay
     inherits that box — the hint wrapped letter-per-line down the margin.
     A placeholder is one word; never let it wrap. */
  white-space:nowrap;
}
.mh-el-row.focused .mh-el-editable{
  box-shadow:0 0 0 2px color-mix(in srgb, var(--indigo) 32%, transparent);
  border-radius:4px;
}
/* Hollywood metrics — FIXED industry column grid (Courier / 10 chars-per-inch).
   The screenplay text area is locked to 60ch (= 6" at 10 cpi), so every element
   sits on a fixed character column that never drifts with window width — the old
   percentage margins (38% / 22% / 30%) drifted and read as "centred", which is
   Asian style, not Hollywood. Columns measured from the text-area left edge:
     action 0–60 · dialogue 10–45 · paren 16–43 · character 22 · transition →60.
   box-sizing:border-box (set globally on the shell) makes the ch padding eat
   into the 60ch box, keeping the grid exact. Font = --script-mono (Courier). */
.hw-action, .hw-character, .hw-dialogue, .hw-paren, .hw-transition, .hw-subtitle{
  font-family:var(--script-mono);
  flex:0 0 auto; width:60ch; max-width:100%;
}
.hw-action{ text-align:left; }
.hw-character{ padding-left:22ch; text-transform:uppercase; font-weight:700; letter-spacing:0.03em; color:var(--tick-character); }
/* A character cue's name is clickable (opens the cast/cue picker). The name-hot
   class is toggled on the row by the NodeView while the pointer is over the name
   glyphs (TipTapSceneEditor onRowMouseMove) so the pointer only shows on the
   name, not the blank space after it. Works for both engines (.mh-el-editable
   covers .hw-character and .as-character). */
.mh-el-row.name-hot .mh-el-editable{ cursor:pointer; }
.hw-dialogue{ padding-left:10ch; padding-right:15ch; }
.hw-paren{ padding-left:16ch; padding-right:17ch; font-style:italic; color:var(--sheet-ink-soft); }
/* A parenthetical WEARS its parentheses (laper/industry): rendered as pseudo
   content so the stored text stays clean — no byte ever changes. Suppressed
   while the block is empty (legacy :empty / PM's trailing-break marker) so
   the type-whisper placeholder never reads "(Parenthetical)".
   @tiptap/react's NodeViewContent wraps the row text in a BLOCK <div>
   (data-node-view-content-react); left block, the '(' / ')' pseudos land on
   their own lines above/below it. Make that wrapper INLINE so the pseudos flow
   inline with the text: '(' before the first character, ')' immediately after
   the LAST character, both wrapping naturally across lines. (The retired
   display:flex kept '(' + content + ')' on ONE line, stranding ')' at the
   first line's end whenever the text wrapped — the multi-line-paren report.) */
.hw-paren:not(:empty):not(:has(br.ProseMirror-trailingBreak)) > *{ display:inline; }
.hw-paren:not(:empty):not(:has(br.ProseMirror-trailingBreak))::before{ content:'('; }
.hw-paren:not(:empty):not(:has(br.ProseMirror-trailingBreak))::after{ content:')'; }
.hw-transition{ text-align:right; text-transform:uppercase; font-weight:700; letter-spacing:0.04em; color:var(--sheet-ink-soft); }
/* Superimpose is a flush-left action line (SUPER: …) per industry standard —
   NOT centred. The gutter tick colour is what distinguishes it. */
.hw-subtitle{ text-align:left; }
/* Comment reads as a QUOTE block (laper parity): a subtle left rule in
   low-saturation ink (not the teal tick colour, which read as a form field),
   italic + faint text. --sheet-ink flips with the theme so the bar stays quiet
   on both cream and dark paper. Same treatment on the Asian engine (.as-comment)
   so the two formats match. */
.hw-comment{ font-family:var(--script-mono); border-left:3px solid color-mix(in srgb, var(--sheet-ink) 22%, transparent); padding-left:10px; color:var(--sheet-ink-soft); font-style:italic; }
/* Transition keeps the SAME col-0 origin as every other row: the tick space is
   preserved (visibility, not display) so its 60ch box starts where the others
   do, then text-align:right pushes the text to column 60. No flex-end reflow. */
.mh-el-row.transition-row{ justify-content:flex-start; }
.mh-el-row.transition-row .mh-el-tick{ visibility:hidden; }

/* ===== ELEMENT ROWS (Asian layout engine) — spec D8 column 2 ===== */
/* A numbered-manuscript typeset: △ action prefix, Name: character label,
   indented dialogue, inline parens, right transitions, quoted comments,
   centred subtitles. Colours come from the theme-scoped variables above, so
   light and dark are both covered without extra selectors. */
.as-row{ display:flex; align-items:baseline; gap:6px; margin:0 0 7px; position:relative; }
.as-row .mh-el-tick{ display:none; }
/* position:static so the abspos gutter below anchors to '.as-row' (sheet column
   0), NOT to '.mh-el-row' — which the per-type wrapper padding shifts right. The
   drop-edge indicator pseudos then span the full row width off '.as-row', which
   is fine (they are full-bleed lines either way). */
.as-row .mh-el-row{ flex:1 1 auto; margin:0; position:static; }
/* Gutter alignment (fix-the-class): the hover gutter (block number + 4-dot grip)
   MUST land in the SAME left-margin column for every element type, exactly like
   Hollywood. The shared in-flow gutter (see IN-FLOW GUTTERS below) pulls itself
   into the margin with a negative margin-left measured from its flex origin —
   '.mh-el-row's content start. In Hollywood that origin is always sheet column 0,
   so every type's gutter aligns. But Asian applies the per-type body indent as
   padding-left on the '.as-row' WRAPPER, which shifts the inner '.mh-el-row' (and
   thus the in-flow gutter) rightward by that indent — so action/dialogue drifted
   +4ch and paren/comment +0.5ch, colliding with the △ mark and the body text (the
   reported bug). Anchor the Asian gutter to '.as-row's padding box instead (its
   left edge is sheet column 0 regardless of padding-left, which only insets the
   CONTENT). left:-51px reproduces the Hollywood column exactly (42px box + the 9px
   row gap = the same -51px the in-flow rule uses); removing it from flow leaves
   the editable's position unchanged because the in-flow gutter's -51px margin
   already consumed net-zero forward space. Width/height/font-size/flex-centring
   are inherited from the base '.mh-el-gutter' rule, so vertical alignment matches
   the first text line. */
.as-row .mh-el-gutter{ position:absolute; left:-51px; top:0; margin-left:0; }
.as-mark{
  font-family:var(--script-hei); font-size:13.5px; line-height:1.7;
  color:var(--sheet-ink-soft); flex-shrink:0; user-select:none;
}
.as-prefix{ color:var(--tick-transition); font-weight:700; }
.as-suffix{ margin-left:-3px; color:var(--tick-character); font-weight:700; }
/* Character cue = left-aligned label, FLUSH LEFT (顶格, 0 indent) — the dialogue
   below it hangs under it (see .as-row-dialogue). The row packs cue + colon to
   the left instead of letting the editable stretch. */
.as-row-character{ justify-content:flex-start; }
.as-row-character .mh-el-row{ flex:0 1 auto; }
.as-row-character .mh-el-editable{ flex:0 1 auto; }
/* Action (△ prefix): the whole text block sits at the 4ch body column with a
   HANGING indent (block padding-left = wrapped lines align at 4ch too), and the
   △ mark hangs one char to the left at 1ch. Absolute so it never widens the row
   or perturbs the .mh-el-row box the paginator measures. */
.as-row-action{ position:relative; padding-left:var(--as-action-body); }
.as-row-action > .as-prefix{
  position:absolute; left:var(--as-action-mark); top:0; margin:0;
}
/* Dialogue hangs under its character cue at the 4ch body column (aligned with
   action). A wrapped dialogue block carries the ∟ continuation mark (国内剧本
   续接记号) hanging at 1.5ch, vertically centred on the block — added as a
   pseudo-element (never editable, never in the doc) and gated on .as-multiline,
   which the NodeView toggles only when the block actually wraps past one line
   (a single-line short line has no ∟, laper parity). */
.as-row-dialogue{ position:relative; padding-left:var(--as-dialogue-body); }
.as-row-dialogue.as-multiline::before{
  content:'\\221F'; /* ∟ U+221F RIGHT ANGLE — manuscript continuation mark */
  position:absolute; left:var(--as-dialogue-mark); top:50%; transform:translateY(-50%);
  font-family:var(--script-hei); color:var(--sheet-ink-soft);
  font-size:13.5px; line-height:1; user-select:none; pointer-events:none;
}
/* Parenthetical: a shallow 0.5ch indent so it tucks just inside the body column. */
.as-row-paren{ padding-left:var(--as-paren-body); }
/* Comment quote block: the left rule sits 0.5ch in, the text at the 4ch body
   column (bar offset via wrapper padding, remaining reach via the editable's own
   padding so text and body column line up). */
.as-row-comment{ padding-left:var(--as-comment-bar); }
.as-row-transition{ justify-content:flex-end; }
.as-row-subtitle{ justify-content:center; }
/* Font split per 分景剧本 convention: 正文(action/dialogue/paren) = 宋体,
   标注/角色名/字幕/转场 = 黑体. Latin chars in these fall through the CJK
   stacks to the sans/serif tail, so mixed EN+ZH stays coherent. */
.as-action{ font-family:var(--script-song); text-align:left; }
.as-character{ font-family:var(--script-hei); font-weight:700; letter-spacing:0.03em; color:var(--tick-character); text-align:left; }
.as-dialogue{ font-family:var(--script-song); text-align:left; }
.as-paren{ font-family:var(--script-song); font-style:italic; color:var(--sheet-ink-soft); }
.as-transition{ font-family:var(--script-hei); text-transform:uppercase; font-weight:700; letter-spacing:0.04em; color:var(--sheet-ink-soft); text-align:right; }
.as-comment{ font-family:var(--script-song); border-left:3px solid color-mix(in srgb, var(--sheet-ink) 22%, transparent); padding-left:calc(var(--as-comment-body) - var(--as-comment-bar)); color:var(--sheet-ink-soft); font-style:italic; }
.as-subtitle{ font-family:var(--script-hei); text-align:center; font-style:italic; color:var(--sheet-ink-soft); }

/* A5/A1: the scene-head badge is the SAME hover-reveal block number in both
   layout engines — no Asian-only chip/flat-number variant (see the shared
   SCREENPLAY CLEAN PAGE rules below, which already cover both formats via the
   plain '.mh-scene-num-badge' selector). */

/* ===== HORIZONTAL MODE-SLOT TOOLBAR ===== */
.mh-h-toolbar{
  display:flex; align-items:center; flex-shrink:0;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:999px; padding:6px 8px; box-shadow:var(--shadow-float); gap:2px;
  flex-wrap:wrap; justify-content:center;
  transition:border-color 0.15s ease, box-shadow 0.15s ease;
}
/* Editing-state hook (Task 6 ②): while a script line is focused the shell root
   carries data-editing="true"; the element toolbar lights up so the writer sees
   the type controls are live for the line under the caret. Pure styling — it
   does not drive focus or a11y, only this visual emphasis. */
.mh-editor-shell[data-editing='true'] .mh-h-toolbar{
  border-color:var(--emph-ink-border);
  box-shadow:var(--shadow-float), 0 0 0 1px var(--emph-ink-glow);
}
.mh-h-item{
  display:flex; align-items:center; gap:6px; padding:7px 13px; border-radius:999px;
  font-size:12px; font-weight:600; color:var(--ink-soft); white-space:nowrap;
  cursor:pointer; background:none; border:none; font-family:var(--sans);
}
.mh-h-item:hover:not(:disabled){ background:var(--surface-2); }
.mh-h-item:disabled{ opacity:0.5; cursor:default; }
.mh-h-item.active{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
/* :hover:not(:disabled) is (0,3,0) and BEATS .active's (0,2,0) while the
   pointer is still on the just-clicked button — the active pill degraded to
   surface-2 with white text (invisible). Re-assert the active look at hover
   specificity. Same guard applied to every state-class button in this sheet. */
.mh-h-item.active:hover:not(:disabled){ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-h-glyph{
  width:16px; height:16px; display:inline-flex; align-items:center; justify-content:center;
  font-size:9.5px; font-family:var(--mono); font-weight:700; color:var(--ink-faint); flex-shrink:0;
}
.mh-h-item.active .mh-h-glyph{ color:var(--pill-ink-on); }

/* ===== RIGHT PANEL WIDGETS ===== */
.mh-field-label{
  font-size:10.5px; font-weight:700; letter-spacing:0.05em; text-transform:uppercase;
  color:var(--ink-faint); margin-bottom:8px;
}
.mh-segmented{ display:flex; background:var(--surface-2); border-radius:10px; padding:3px; gap:2px; }
.mh-seg{
  flex:1; text-align:center; font-size:12px; font-weight:600; padding:7px 0; border-radius:8px;
  color:var(--ink-soft); cursor:pointer; background:none; border:none; font-family:var(--sans);
}
.mh-seg.active{ background:var(--pill-ink-bg); color:var(--pill-ink-on); }
.mh-seg:disabled{ opacity:0.45; cursor:default; }
.mh-zoom{ display:flex; align-items:stretch; background:var(--surface-2); border-radius:10px; padding:3px; gap:2px; }
.mh-zoom-btn{
  width:34px; display:flex; align-items:center; justify-content:center;
  font-size:16px; font-weight:600; border-radius:8px; line-height:1;
  color:var(--ink-soft); cursor:pointer; background:none; border:none; font-family:var(--sans);
}
.mh-zoom-btn:hover:not(:disabled){ background:var(--surface); color:var(--ink); }
.mh-zoom-btn:disabled{ opacity:0.35; cursor:default; }
.mh-zoom-value{
  flex:1; text-align:center; font-size:12.5px; font-weight:600; padding:7px 0; border-radius:8px;
  color:var(--ink); cursor:pointer; background:none; border:none; font-family:var(--sans);
  font-variant-numeric:tabular-nums;
}
.mh-zoom-value:hover{ background:var(--surface); }
.mh-divider{ height:1px; background:var(--surface-border); margin:0 -16px; }
.mh-stats-grid{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
/* laper parity: stat tiles are a quiet warm-grey card with a deep-ink number,
   not the old indigo-soft / violet-soft colour blocks. The .v2 tint is dropped
   (all four tiles read as one neutral set) — the numbers carry the emphasis. */
.mh-stat-tile{
  background:var(--surface-2); border:1px solid var(--surface-border);
  border-radius:12px; padding:12px 12px 10px;
}
.mh-stat-tile.v2{ background:var(--surface-2); }
.mh-stat-num{ font-size:19px; font-weight:800; color:var(--ink); font-family:var(--mono); }
.mh-stat-tile.v2 .mh-stat-num{ color:var(--ink); }
.mh-stat-lbl{ font-size:10.5px; color:var(--ink-faint); font-weight:600; margin-top:2px; }
.mh-cast-row{ display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink-soft); padding:3px 0; }
.mh-cast-dot{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }
/* A1/A5: the scene-head number is the SAME hover-reveal typography as the
   per-element '.mh-el-num' block number (no dark ink chip) — final absolute
   position + hover-reveal opacity live in the SCREENPLAY CLEAN PAGE block
   below, this is the shared type spec both themes inherit unstyled. */
.mh-scene-num-badge{
  font-family:var(--mono); font-size:10.5px; font-weight:600;
  font-variant-numeric:tabular-nums; color:var(--sheet-ink-soft);
  flex-shrink:0;
}
.mh-scene-chip{ font-size:11px; font-weight:700; letter-spacing:0.03em; padding:4px 9px; border-radius:6px; font-family:var(--mono); }
.mh-scene-chip.ie-int{ background:var(--chip-ink-bg); color:var(--chip-ink-fg); }
.mh-scene-chip.ie-ext{ background:var(--chip-ink-bg); color:var(--chip-ink-fg); }
.mh-scene-chip.loc{ background:var(--surface-2); color:var(--sheet-ink-soft); border:1px solid var(--sheet-border); }
.mh-el-line{ font-size:13.5px; line-height:1.7; color:var(--sheet-ink); margin-bottom:7px; }
.mh-placeholder-line{ color:var(--sheet-ink-soft); font-style:italic; }
/* Empty-scene seed affordance: a real click/keyboard target that materialises
   the scene's first element (see EmptySceneHint). Reads as a faint clickable
   line, not dead placeholder text. */
.mh-placeholder-seed{ cursor:text; border-radius:4px; padding:1px 4px; margin-left:-4px; transition:background 0.12s ease; }
.mh-placeholder-seed:hover{ background:var(--surface-2); }
.mh-placeholder-seed:focus-visible{ outline:2px solid var(--indigo); outline-offset:-2px; }

/* ===== @ MENTION CHIPS + PICKER ===== */
.mh-mention{
  color:var(--indigo-deep); background:var(--indigo-soft);
  border-radius:5px; padding:0 4px; font-weight:600;
  box-decoration-break:clone; -webkit-box-decoration-break:clone;
}
.mh-mention.unknown{ color:var(--ink-faint); background:var(--surface-2); font-weight:500; }
/* Editor popups follow laper's paper-toned dropdown: a near-white elevated
   panel, hairline border, soft large-radius shadow, and a quiet WARM-grey
   hover/selection derived from the sheet's own ink — no brand-indigo one-size
   fill. The selected row is darker-grey-on-paper with black ink (laper), so
   keyboard position stays visible without shouting a colour. */
.mh-mention-pop{
  z-index:20; min-width:200px; max-width:300px; margin-top:2px;
  /* Elevated chrome surface (not a hardcoded #fff) so the cue picker flips with
     the editor theme instead of flashing white on dark paper. */
  background:var(--surface); border:1px solid var(--sheet-border);
  border-radius:10px; box-shadow:0 10px 28px rgba(35,20,90,0.14);
  padding:4px; font-family:var(--sans);
}
.mh-mention-list{ list-style:none; margin:0; padding:0; max-height:228px; overflow-y:auto; }
.mh-mention-opt{
  padding:8px 12px; border-radius:6px; font-size:13px;
  color:var(--sheet-ink); cursor:pointer; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis;
  transition:background 0.12s ease;
}
.mh-mention-opt:hover{ background:color-mix(in srgb, var(--sheet-ink) 6%, transparent); }
.mh-mention-opt.active{ background:color-mix(in srgb, var(--sheet-ink) 11%, transparent); color:var(--sheet-ink); }
.mh-mention-opt[aria-selected='true']{ font-weight:600; }
/* "Create <name>" row — same search-or-create affordance as .mh-heading-opt.create
   (the location field), so coining a new cue reads identically across the sheet. */
.mh-mention-opt.create{ font-weight:600; color:var(--sheet-ink); }
.mh-mention-empty{ padding:8px 12px; font-size:12.5px; color:var(--sheet-ink-soft); }
/* Embedded search row (character-cue picker, laper parity): full-bleed strip
   at the panel top, separated by a hairline. The input is chromeless — the
   strip IS the field. */
.mh-mention-search{
  display:flex; align-items:center; gap:8px;
  margin:-4px -4px 4px; padding:9px 12px;
  border-bottom:1px solid var(--sheet-border);
  color:var(--sheet-ink-soft);
}
.mh-mention-search input{
  flex:1; min-width:0; background:none; border:none; outline:none;
  font-family:var(--sans); font-size:13px; color:var(--sheet-ink); padding:0;
}
.mh-mention-search input::placeholder{ color:var(--sheet-ink-soft); }
/* kbd-hint footer — same affordance as the sheet's .mh-keyboard-hint. */
.mh-mention-hints{
  display:flex; gap:14px; margin:4px -4px -4px; padding:8px 12px;
  border-top:1px solid var(--sheet-border);
}
.mh-mention-hint{
  display:flex; align-items:center; gap:6px;
  font-size:11.5px; color:var(--sheet-ink-soft); white-space:nowrap;
}
.mh-pop-kbd{
  font-family:var(--mono); font-size:10px; line-height:1; font-weight:600;
  padding:3px 5px; border-radius:4px;
  background:color-mix(in srgb, var(--sheet-ink) 5%, transparent);
  border:1px solid var(--sheet-border);
  color:var(--sheet-ink-soft);
}

/* Outline / Cover placeholders (read-only in Phase 1) */
.mh-doc-outline{ font-family:var(--sans); color:var(--sheet-ink); }
.mh-doc-title{ font-size:25px; font-weight:800; letter-spacing:-0.01em; margin-bottom:20px; }
.mh-doc-p{ font-size:13.5px; line-height:1.75; color:var(--sheet-ink-soft); }

/* ===== OUTLINE VIEW (Outline↔Script linkage tree, Task 4) ===== */
/* Clear document hierarchy: chapter titles read as H1 (bold, larger, generous
   spacing); scene rows sit indented beneath as lighter H2/body-weight rows. */
.mh-outline{ font-family:var(--sans); color:var(--sheet-ink); }
.mh-outline-group{ margin-bottom:30px; }
.mh-outline-group:last-child{ margin-bottom:0; }
.mh-outline-chapter{
  padding:2px 8px 10px; margin-bottom:8px;
  border-bottom:1px solid var(--sheet-border);
  border-radius:var(--radius-sm) var(--radius-sm) 0 0;
}
.mh-outline-group.drop-active .mh-outline-chapter{
  background:var(--indigo-soft); border-bottom-color:var(--indigo);
}
.mh-outline-chapter-title{
  margin:0; font-size:22px; font-weight:800; letter-spacing:-0.01em;
  line-height:1.25; color:var(--sheet-ink);
}
.mh-outline-chapter-excerpt{
  margin:6px 0 0; font-size:12.5px; line-height:1.55; color:var(--sheet-ink-soft);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-outline-scenes{ list-style:none; margin:0; padding:0; }
.mh-outline-scene-wrap{ position:relative; padding-left:22px; }
.mh-outline-scene-row{
  display:flex; align-items:baseline; gap:10px;
  padding:8px 12px; border-radius:var(--radius-sm);
  cursor:pointer; border:1px solid transparent;
}
.mh-outline-scene-row:hover{ background:var(--surface-2); }
.mh-outline-scene-row:focus-visible{ outline:2px solid var(--indigo); outline-offset:-2px; }
.mh-outline-scene-row.dragging{ opacity:0.55; }
.mh-outline-scene-num{
  flex-shrink:0; min-width:20px; font-family:var(--mono);
  font-size:11px; font-weight:700; color:var(--ink-faint);
}
.mh-outline-scene-heading{
  flex-shrink:0; font-family:var(--mono); font-size:11.5px; font-weight:700;
  letter-spacing:0.03em; text-transform:uppercase; color:var(--sheet-ink);
}
.mh-outline-scene-summary{
  font-size:13px; color:var(--sheet-ink-soft);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-outline-empty{
  padding:4px 12px 4px 22px; font-size:12px; color:var(--ink-faint); font-style:italic;
}

/* Unstarted (orphan) chapters — quiet secondary section beneath the real
   outline groups (A3: moved out of the Script tab, these are not script
   content, just a "hasn't been split into scenes yet" stub). */
.mh-outline-orphan-chapters{
  margin-top:36px; padding-top:20px; border-top:1px dashed var(--sheet-border);
}
.mh-outline-orphan-heading{
  margin:0 0 12px; font-size:12px; font-weight:700; letter-spacing:0.04em;
  text-transform:uppercase; color:var(--ink-faint);
}
.mh-outline-orphan-chapters .mh-chapter-fallback{ margin-bottom:14px; }
.mh-outline-orphan-chapters .mh-chapter-fallback:last-child{ margin-bottom:0; }

.mh-cover-card{
  width:820px; max-width:100%; min-height:60%;
  background:var(--surface); border:1px dashed var(--surface-border);
  border-radius:var(--radius-lg); display:flex; align-items:center; justify-content:center;
  color:var(--ink-faint); font-size:14px; font-weight:600; text-align:center; padding:40px;
}

.mh-keyboard-hint{
  flex-shrink:0; margin-top:10px; display:flex; align-items:center; justify-content:center; gap:6px;
  font-size:11px; color:var(--ink-faint); font-family:var(--mono);
}
.mh-keyboard-hint kbd{
  background:var(--surface); border:1px solid var(--surface-border); border-radius:5px;
  padding:2px 6px; font-family:var(--mono); font-size:10.5px; color:var(--ink-soft);
}
.mh-keyboard-hint .sep{ color:var(--surface-border); }

/* ===== SAVE INDICATOR ===== */
.mh-save-indicator{
  display:flex; align-items:center; gap:6px; font-size:12px; font-weight:600;
  border-radius:20px; padding:6px 12px 6px 10px; min-height:30px;
  color:var(--ink-soft); background:var(--surface-2);
}
.mh-save-dot{ width:7px; height:7px; border-radius:50%; background:currentColor; }
.mh-save-check{ font-size:12px; line-height:1; }
.mh-save-count{
  margin-left:2px; font-family:var(--mono); font-size:11px; font-weight:700;
  min-width:16px; text-align:center;
}
.mh-save-indicator.saved{ color:var(--green); background:var(--green-soft); }
.mh-save-indicator.saving{ color:var(--indigo-deep); background:var(--indigo-soft); }
.mh-save-indicator.saving .mh-save-dot{ animation:mh-save-pulse 1.1s ease-in-out infinite; }
.mh-save-indicator.retrying{ color:var(--amber); background:var(--amber-soft); }
.mh-save-indicator.offline{ color:var(--ink-faint); background:var(--surface-2); }
.mh-save-indicator.conflict{ color:var(--red); background:color-mix(in srgb, var(--red) 12%, transparent); }
@keyframes mh-save-pulse{ 0%,100%{ opacity:1; } 50%{ opacity:0.35; } }

/* ===== CONFLICT BAR (409 resolution) ===== */
.mh-conflict-bar{
  flex-shrink:0; width:820px; max-width:100%;
  display:flex; align-items:center; justify-content:space-between; gap:14px;
  padding:9px 14px; border-radius:var(--radius-md);
  background:color-mix(in srgb, var(--red) 10%, var(--surface));
  border:1px solid color-mix(in srgb, var(--red) 40%, var(--surface-border));
  color:var(--ink); font-size:12.5px; font-weight:600;
}
.mh-conflict-msg{ min-width:0; }
.mh-conflict-actions{ display:flex; gap:8px; flex-shrink:0; }
.mh-conflict-btn{
  font-family:var(--sans); font-size:12px; font-weight:600; padding:6px 12px;
  border-radius:var(--radius-sm); cursor:pointer;
  background:var(--red); color:#fff; border:1px solid transparent;
}
.mh-conflict-btn.ghost{ background:var(--surface); color:var(--ink-soft); border-color:var(--surface-border); }
.mh-conflict-btn:hover{ filter:brightness(0.96); }

/* ===== COLD START ===== */
.mh-coldstart{
  margin:auto; max-width:460px; text-align:center;
  display:flex; flex-direction:column; align-items:center; gap:12px; padding:48px 24px;
}
.mh-coldstart-title{ font-size:22px; font-weight:800; color:var(--ink); letter-spacing:-0.01em; }
.mh-coldstart-sub{ font-size:13.5px; line-height:1.7; color:var(--ink-faint); margin:0; }
.mh-coldstart-btn{
  margin-top:6px; font-family:var(--sans); font-size:13px; font-weight:700;
  padding:11px 22px; border-radius:var(--radius-md); cursor:pointer;
  background:var(--indigo); color:var(--accent-on); border:none;
  box-shadow:var(--shadow-island);
}
.mh-coldstart-btn:hover{ background:var(--indigo-deep); }
.mh-coldstart-actions{ margin-top:6px; display:flex; gap:10px; align-items:center; }
.mh-coldstart-actions .mh-coldstart-btn{ margin-top:0; }
.mh-coldstart-btn-secondary{
  background:transparent; color:var(--ink); border:1px solid var(--hairline);
  box-shadow:none;
}
.mh-coldstart-btn-secondary:hover{ background:var(--surface-2); }

/* ===== RIGHT PANEL ===== */
.mh-right-col{ width:296px; display:flex; flex-direction:column; min-height:0; transition:width 0.2s ease; }
.mh-right-col.collapsed{ width:56px; }
.mh-panel-head{ padding:16px 16px 4px; display:flex; align-items:center; justify-content:space-between; }
.mh-panel-title{ font-size:13px; font-weight:700; color:var(--ink); }
.mh-panel-body{ padding:12px 16px 18px; display:flex; flex-direction:column; gap:14px; overflow-y:auto; }
.mh-panel-collapsed-strip{ padding:14px 0; display:flex; flex-direction:column; align-items:center; gap:14px; }
.mh-panel-hint{ font-size:12px; color:var(--ink-faint); line-height:1.6; }

/* ===== LOADING / ERROR ===== */
.mh-shell-state{
  position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  flex-direction:column; gap:10px; color:var(--ink-soft);
  /* Opaque overlay that must read as the SAME paper-neutral surface as the
     shell behind it — reuse the shell's own radial-gradient (var(--bg-2)→
     var(--bg)) so "Loading scene…" no longer flashes a flat cold-lavender
     block distinct from the editor region. */
  background:radial-gradient(circle at 15% 0%, var(--bg-2) 0%, var(--bg) 55%);
  font-family:var(--sans); font-size:14px; padding:24px; text-align:center;
  /* Sits ABOVE the paper/rail while loading. Without this it paints behind
     .mh-center-col (position:relative, later in DOM), so the empty paper shows
     through the overlay and a non-empty script flashes its blank/empty state
     during the scene fetch. Above the sticky element toolbar (z-index:40) so
     the whole editor region reads as a single clean "Loading…" state. */
  z-index:50;
}
.mh-shell-state.error{ color:var(--red); }

/* ===== SCENE REORDER (drag + keyboard, Task 10) ===== */
/* cursor/border-radius/hover states live on the base .mh-drag-handle rule
   above (A5: shares its full visual spec with the element-row .mh-el-drag
   handle) — nothing scene-reorder-specific to add here beyond the dim. */
.mh-scene-block.dragging{ opacity:0.55; }
.mh-drop-indicator{
  height:2px; border-radius:2px; margin:5px 0;
  background:var(--indigo); box-shadow:0 0 0 1px color-mix(in srgb, var(--indigo) 30%, transparent);
}
.mh-drop-indicator.before{ margin-top:0; }
.mh-drop-indicator.after{ margin-bottom:0; }

/* Windowed-render placeholder (unmounted far-off scenes keep scroll geometry). */
.mh-scene-placeholder{ margin-bottom:26px; }

/* ===== LEGACY CHAPTER FALLBACK (read-only prose + Convert) ===== */
.mh-chapter-fallback{
  margin:0 0 26px; padding:16px 18px;
  background:var(--surface-2); border:1px solid var(--sheet-border);
  border-radius:var(--radius-md); border-left:3px solid var(--tick-transition);
}
.mh-chapter-fallback-head{
  display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:8px;
}
.mh-chapter-fallback-title{
  margin:0; font-size:14px; font-weight:700; color:var(--sheet-ink); font-family:var(--sans);
}
.mh-chapter-convert-btn{
  flex-shrink:0; font-family:var(--sans); font-size:12px; font-weight:600;
  padding:6px 12px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--indigo); color:var(--accent-on); border:1px solid transparent;
}
.mh-chapter-convert-btn:hover:not(:disabled){ background:var(--indigo-deep); }
.mh-chapter-convert-btn:disabled{ opacity:0.55; cursor:default; }
.mh-chapter-fallback-body{
  margin:0; font-size:13px; line-height:1.7; color:var(--sheet-ink-soft);
  white-space:pre-wrap; font-family:var(--mono);
}
.mh-chapter-fallback-body.empty{ font-style:italic; }

/* ===== VISIBLE FOCUS RING AUDIT (a11y sweep, Task 10) =====
   NOTE: .mh-el-editable is deliberately NOT in this group. Chrome treats a
   focused contenteditable as :focus-visible ALWAYS (even on mouse click), so
   ringing it drew a permanent indigo box around the script line the writer is
   typing in — the "empty input box" the user kept reporting. A text-editing
   surface's focus indicator is its blinking caret (Docs/Notion do the same);
   buttons/selects/tabs keep the ring. */
.mh-editor-shell button:focus-visible,
.mh-editor-shell select:focus-visible,
.mh-editor-shell input:focus-visible,
.mh-editor-shell [role='tab']:focus-visible,
.mh-editor-shell [role='option']:focus-visible{
  outline:2px solid var(--indigo);
  outline-offset:2px;
  border-radius:5px;
}
.mh-editor-shell .mh-el-editable:focus-visible{ outline:none; }
.mh-editor-shell .mh-drag-handle:focus-visible{ opacity:1; outline-offset:1px; }

/* ===== COPILOT SUMMON — gutter-tick select + card (Task 11) ===== */
.mh-el-tick.tick-btn{
  width:5px; padding:0; border:none; cursor:pointer; appearance:none;
  transition:box-shadow 0.12s ease, transform 0.12s ease;
}
.mh-el-tick.tick-btn:hover{ transform:scaleX(1.6); }
.mh-el-tick.tick-btn.selected{
  transform:scaleX(1.6);
  box-shadow:0 0 0 2px color-mix(in srgb, var(--indigo) 45%, transparent);
}

.mh-copilot-card{
  margin:10px 0 4px; max-width:520px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); box-shadow:var(--shadow-float);
  padding:12px 14px; display:flex; flex-direction:column; gap:10px;
}
.mh-copilot-head{ display:flex; align-items:center; gap:9px; }
.mh-copilot-badge{
  font-size:10.5px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase;
  padding:3px 8px; border-radius:999px;
  background:var(--indigo-soft); color:var(--indigo-deep);
}
.mh-copilot-target{ font-size:12.5px; font-weight:600; color:var(--ink-soft); }
.mh-copilot-actions{ display:flex; gap:8px; flex-wrap:wrap; }
.mh-copilot-btn{
  font-family:var(--sans); font-size:12px; font-weight:600; padding:7px 13px;
  border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface-2); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-copilot-btn.primary{ background:var(--indigo); color:var(--accent-on); border-color:transparent; }
.mh-copilot-btn.primary:hover:not(:disabled){ background:var(--indigo-deep); }
.mh-copilot-btn:disabled{ opacity:0.5; cursor:default; }
.mh-copilot-input{
  font-family:var(--sans); font-size:12.5px; padding:8px 11px;
  border-radius:var(--radius-sm); border:1px solid var(--surface-border);
  background:var(--surface-2); color:var(--ink-soft);
}
.mh-copilot-input:disabled{ opacity:0.6; cursor:default; }
.mh-copilot-result{
  display:flex; align-items:center; justify-content:space-between; gap:10px;
  font-size:12.5px; font-weight:600; color:var(--green);
}
.mh-copilot-result.failed{ color:var(--red); }
.mh-copilot-undo{
  font-family:var(--sans); font-size:12px; font-weight:600; padding:5px 11px;
  border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-copilot-undo:hover{ background:var(--surface-2); }

/* ===== NODE VIEW (scene/chapter flow projection) ===== */
.mh-nodes-view{
  flex:1; min-height:0; min-width:0; position:relative;
  /* Transparent so the shell's own paper-neutral gradient is the canvas floor
     (matches the script region's transparent .mh-sheet-scroll) instead of a
     distinct cold-lavender --surface-2 panel. The dot grid + controls below
     stay tinted to the theme vars, so the grain follows light/dark. */
  background:transparent; border:1px solid var(--surface-border);
  border-radius:var(--radius-lg); overflow:hidden;
}
/* Empty state — no scenes and no chapters to project (ReactFlow unmounted). */
.mh-nodes-empty{
  position:absolute; inset:0; margin:auto; max-width:420px; height:100%;
  display:flex; flex-direction:column; align-items:center; justify-content:center;
  gap:10px; text-align:center; padding:24px;
}
.mh-nodes-empty-title{ font-size:16px; font-weight:800; color:var(--ink); letter-spacing:-0.01em; }
.mh-nodes-empty-sub{ font-size:13px; line-height:1.65; color:var(--ink-faint); margin:0; }
.mh-nodes-empty-btn{
  margin-top:6px; font-family:var(--sans); font-size:12.5px; font-weight:700;
  padding:9px 18px; border-radius:var(--radius-md); cursor:pointer;
  background:var(--indigo); color:var(--accent-on); border:none; box-shadow:var(--shadow-island);
}
.mh-nodes-empty-btn:hover{ background:var(--indigo-deep); }
/* React Flow chrome tinted to the editor palette (dots + controls). */
.mh-nodes-view .react-flow__background{ color:var(--surface-border); }
.mh-nodes-view .react-flow__controls{
  box-shadow:var(--shadow-island); border-radius:var(--radius-sm); overflow:hidden;
}
.mh-nodes-view .react-flow__controls-button{
  background:var(--surface); border-bottom:1px solid var(--surface-border);
  color:var(--ink-soft); fill:var(--ink-soft);
}
.mh-nodes-view .react-flow__controls-button:hover{ background:var(--surface-2); }
.mh-nodes-view .react-flow__minimap{
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-sm); box-shadow:var(--shadow-island);
}
/* Alignment guides — a passive overlay spanning the whole canvas. */
.mh-flow-guides{ position:absolute; inset:0; pointer-events:none; z-index:5; }
.mh-flow-guide{ position:absolute; background:var(--indigo); }
.mh-flow-guide-v{ top:0; bottom:0; width:1px; }
.mh-flow-guide-h{ left:0; right:0; height:1px; }
.mh-flow-node{
  width:220px; background:var(--surface); color:var(--ink);
  border:1px solid var(--surface-border); border-radius:var(--radius-md);
  box-shadow:var(--shadow-island); padding:11px 13px 12px; font-family:var(--sans);
}
.mh-flow-handle{ width:7px; height:7px; background:var(--ink-faint); border:none; }
.mh-flow-scene-cover{
  display:block; width:100%; height:72px; object-fit:cover;
  border-radius:var(--radius-sm); border:1px solid var(--surface-border);
  margin-bottom:9px;
}
.mh-flow-scene-head{ display:flex; align-items:center; gap:8px; }
.mh-flow-scene-heading{
  font-size:11px; font-weight:700; letter-spacing:0.02em; text-transform:uppercase;
  color:var(--ink-soft); font-family:var(--mono); overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap;
}
.mh-flow-scene-summary{
  margin-top:8px; font-size:12.5px; line-height:1.5; color:var(--ink);
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}
.mh-flow-scene-foot{ margin-top:9px; display:flex; }
.mh-flow-pill{
  font-size:10.5px; font-weight:600; font-family:var(--mono);
  padding:2px 8px; border-radius:999px;
  background:var(--chip-ink-bg); color:var(--chip-ink-fg);
}
.mh-flow-chapter{ width:200px; background:var(--surface-2); border-color:var(--surface-border); }
.mh-flow-chapter-head{ display:flex; align-items:center; gap:8px; }
.mh-flow-chapter-title{ font-size:13px; font-weight:700; color:var(--ink); }
.mh-flow-chapter-summary{
  margin-top:7px; font-size:12px; line-height:1.5; color:var(--ink-soft);
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden;
}
.mh-flow-chapter[aria-busy='true']{ opacity:0.75; }
.mh-flow-actions{ margin-top:10px; display:flex; flex-wrap:wrap; gap:6px; }
.mh-flow-action{
  font-family:var(--sans); font-size:11px; font-weight:600;
  padding:4px 9px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-flow-action:hover:not(:disabled){ background:var(--surface-2); color:var(--ink); }
.mh-flow-action:disabled{ opacity:0.5; cursor:default; }
.mh-flow-action.confirming{
  background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent;
}

/* ===== STORYBOARD VIEW (scene columns + shot cards) ===== */
.mh-storyboard{
  flex:1; min-height:0; min-width:0;
  /* Transparent board — the scene columns are white --surface cards floating on
     the shell's paper-neutral gradient (parity with the script paper), not a
     cold-lavender --surface-2 slab. */
  background:transparent; border:1px solid var(--surface-border);
  border-radius:var(--radius-lg); padding:16px;
  display:flex; gap:16px; align-items:flex-start;
  overflow-x:auto; overflow-y:hidden;
}
.mh-sb-column{
  flex:0 0 300px; max-height:100%; min-height:0;
  display:flex; flex-direction:column; gap:10px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); box-shadow:var(--shadow-island); padding:12px;
}
.mh-sb-col-head{ display:flex; align-items:center; gap:9px; }
.mh-sb-col-heading{
  flex:1; min-width:0; font-family:var(--mono);
  font-size:11.5px; font-weight:700; letter-spacing:0.03em; text-transform:uppercase;
  color:var(--ink); white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-sb-auto-btn{
  flex-shrink:0; font-family:var(--sans); font-size:11px; font-weight:600;
  padding:5px 10px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface-2); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-sb-auto-btn:hover:not(:disabled){ background:var(--hover-ink-bg); color:var(--ink); }
.mh-sb-auto-btn:disabled{ opacity:0.6; cursor:default; }
.mh-sb-auto-btn.confirming{ background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent; }
.mh-sb-shots{ flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding:2px; }
.mh-sb-empty{ padding:14px 8px; font-size:12px; color:var(--ink-faint); font-style:italic; text-align:center; }
.mh-sb-empty-board{ margin:auto; font-size:13px; color:var(--ink-faint); font-style:italic; }
.mh-sb-add{
  flex-shrink:0; font-family:var(--sans); font-size:12px; font-weight:600;
  padding:8px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface-2); color:var(--ink-soft);
  border:1px dashed var(--surface-border);
}
.mh-sb-add:hover{ background:var(--hover-ink-bg); color:var(--ink); border-color:var(--emph-ink-border); }

/* Shot card */
.mh-shot-card-wrap{ position:relative; }
.mh-shot-card{
  display:flex; flex-direction:column; gap:8px; padding:10px;
  background:var(--surface-2); border:1px solid var(--surface-border);
  border-radius:var(--radius-sm); cursor:grab;
}
.mh-shot-card:active{ cursor:grabbing; }
.mh-shot-card.dragging{ opacity:0.55; }
.mh-shot-head{ display:flex; align-items:center; gap:8px; }
.mh-shot-status-corner{ flex:1; min-width:0; display:flex; align-items:center; }
.mh-shot-status{ font-family:var(--mono); font-size:10.5px; font-weight:600; }
.mh-shot-status.empty{ color:var(--ink-faint); }
.mh-shot-status.generating{
  display:inline-flex; align-items:center; gap:6px; color:var(--indigo-deep);
}
.mh-shot-status.failed{ color:var(--red); }
.mh-shot-pulse{
  width:7px; height:7px; border-radius:50%; background:currentColor;
  animation:mh-save-pulse 1.1s ease-in-out infinite;
}
.mh-shot-thumb{
  width:auto; height:34px; max-width:100%; border-radius:5px;
  border:1px solid var(--surface-border); object-fit:cover;
}
.mh-shot-delete{
  flex-shrink:0; width:24px; height:24px; border-radius:var(--radius-sm);
  display:flex; align-items:center; justify-content:center; cursor:pointer;
  background:none; border:1px solid transparent; color:var(--ink-faint);
  font-size:15px; font-family:var(--sans);
}
.mh-shot-delete:hover{ background:var(--surface); color:var(--red); }
.mh-shot-delete.confirming{
  width:auto; padding:0 8px; font-size:11px; font-weight:600;
  background:var(--red); color:#fff;
}
.mh-shot-pills{ display:flex; flex-wrap:wrap; gap:5px; align-items:center; }
.mh-shot-pill{
  font-family:var(--mono); font-size:10px; font-weight:700; letter-spacing:0.02em;
  padding:3px 8px; border-radius:999px; cursor:pointer;
  background:var(--chip-ink-bg); color:var(--chip-ink-fg); border:1px solid transparent;
}
.mh-shot-pill:hover{ border-color:var(--emph-ink-border); }
.mh-shot-pill.empty{ background:var(--surface); color:var(--ink-faint); }
.mh-shot-focal{
  width:60px; font-family:var(--mono); font-size:10px; font-weight:700;
  padding:3px 6px; border-radius:6px;
  border:1px solid var(--surface-border); background:var(--surface); color:var(--ink);
}
.mh-shot-lighting{
  font-size:11px; line-height:1.5; color:var(--ink-soft); font-style:italic;
}
.mh-shot-desc{
  width:100%; resize:vertical; font-family:var(--sans); font-size:12px; line-height:1.5;
  padding:6px 8px; border-radius:6px;
  border:1px solid var(--surface-border); background:var(--surface); color:var(--ink);
}
.mh-shot-desc:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
.mh-shot-foot{ display:flex; gap:6px; }
.mh-shot-generate{
  flex:1; font-family:var(--sans); font-size:11.5px; font-weight:600;
  padding:6px 10px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-shot-generate:hover:not(:disabled){ background:var(--hover-ink-bg); color:var(--ink); border-color:var(--emph-ink-border); }
.mh-shot-generate:disabled{ opacity:0.5; cursor:default; }
.mh-shot-generate-video{
  flex:1; font-family:var(--sans); font-size:11.5px; font-weight:600;
  padding:6px 10px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-shot-generate-video:hover:not(:disabled){ background:var(--hover-ink-bg); color:var(--ink); border-color:var(--emph-ink-border); }
.mh-shot-generate-video:disabled{ opacity:0.5; cursor:default; }
.mh-shot-generate-video.confirming{ background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent; }
.mh-shot-video{
  width:100%; max-width:100%; border-radius:6px; margin-top:2px;
  border:1px solid var(--surface-border); background:#000; display:block;
}

/* ── Version history panel (right island top section, Phase B P4) ─────────── */
.mh-version-panel{ display:flex; flex-direction:column; gap:8px; }
.mh-version-head{ display:flex; align-items:center; justify-content:space-between; gap:8px; }
.mh-version-save-btn{
  font-family:var(--sans); font-size:11.5px; font-weight:600; cursor:pointer;
  padding:4px 10px; border-radius:var(--radius-sm);
  background:var(--indigo); color:var(--accent-on); border:1px solid transparent;
}
.mh-version-save-btn:hover{ background:var(--indigo-deep); }
.mh-version-input-row{ display:flex; align-items:center; gap:6px; }
.mh-version-input{
  flex:1; min-width:0; font-family:var(--sans); font-size:12px;
  padding:6px 8px; border-radius:6px;
  border:1px solid var(--surface-border); background:var(--surface); color:var(--ink);
}
.mh-version-input:focus-visible{ outline:2px solid var(--indigo); outline-offset:1px; }
.mh-version-input-save{
  flex-shrink:0; font-family:var(--sans); font-size:11.5px; font-weight:600; cursor:pointer;
  padding:5px 10px; border-radius:var(--radius-sm);
  background:var(--indigo); color:var(--accent-on); border:1px solid transparent;
}
.mh-version-input-save:disabled{ opacity:0.5; cursor:default; }
.mh-version-input-cancel{
  flex-shrink:0; width:24px; height:24px; border-radius:var(--radius-sm);
  display:flex; align-items:center; justify-content:center; cursor:pointer;
  background:none; border:1px solid transparent; color:var(--ink-faint);
  font-size:16px; font-family:var(--sans);
}
.mh-version-input-cancel:hover:not(:disabled){ background:var(--surface-2); color:var(--ink); }
.mh-version-list{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
/* A long history must never stretch the Writing island: collapsed shows the
   newest COLLAPSED_COUNT; "Show all" expands into an internally-scrolling
   capped list. */
.mh-version-list.expanded{ max-height:300px; overflow-y:auto; }
.mh-version-showall{
  align-self:flex-start; margin-top:2px; cursor:pointer;
  font-family:var(--sans); font-size:11.5px; font-weight:600;
  color:var(--ink-soft); background:none; border:none; padding:2px 0;
}
.mh-version-showall:hover{ text-decoration:underline; color:var(--ink); }
/* COMPACT single-row cards (user: a tall card per version stretches the
   island): message + time on one line; the action pills float in over the
   row's right side only on hover/focus (absolutely positioned with the
   row's own background, so revealing them never shifts layout and the row
   stays one line tall even in a fully expanded list). */
.mh-version-item{
  display:flex; flex-direction:row; align-items:center; flex-wrap:wrap;
  gap:8px; padding:5px 10px; min-height:32px;
  border:1px solid var(--surface-border); border-radius:var(--radius-sm);
  background:var(--surface-2);
  position:relative;
}
.mh-version-item-main{
  display:flex; flex-direction:row; align-items:baseline; gap:8px;
  flex:1; min-width:0;
}
.mh-version-msg{
  font-size:12.5px; font-weight:600; color:var(--ink);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.mh-version-meta{ display:flex; align-items:baseline; gap:8px; flex-shrink:0; }
.mh-version-author{
  font-family:var(--sans); font-size:10.5px; font-weight:600; color:var(--ink-soft);
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:96px;
}
.mh-version-time{ font-family:var(--mono); font-size:10.5px; color:var(--ink-faint); flex-shrink:0; }
.mh-version-actions{
  display:flex; flex-wrap:nowrap; gap:5px;
  position:absolute; top:4px; right:5px; padding-left:14px;
  background:linear-gradient(90deg, transparent, var(--surface-2) 12px);
  opacity:0; pointer-events:none; transition:opacity 0.12s ease;
}
.mh-version-item:hover .mh-version-actions,
.mh-version-item:focus-within .mh-version-actions{ opacity:1; pointer-events:auto; }
.mh-version-partial{ width:100%; }
.mh-version-action{
  font-family:var(--sans); font-size:11px; font-weight:600; cursor:pointer;
  padding:3px 9px; border-radius:999px;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-version-action:hover:not(:disabled){ background:var(--hover-ink-bg); color:var(--ink); border-color:color-mix(in srgb, var(--ink) 22%, var(--surface-border)); }
.mh-version-action:disabled{ opacity:0.5; cursor:default; }
.mh-version-action.confirming{ background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent; }
.mh-version-action.confirming:hover:not(:disabled){ background:var(--pill-ink-bg); color:var(--pill-ink-on); border-color:transparent; }
.mh-version-action.danger:hover:not(:disabled){ background:var(--red); color:#fff; border-color:transparent; }
.mh-version-action.danger.confirming{ background:var(--red); color:#fff; border-color:transparent; }
.mh-version-partial{
  padding:8px; border-radius:var(--radius-sm);
  background:var(--red-soft,var(--surface)); border:1px solid var(--red);
}
.mh-version-partial-head{ font-size:11.5px; font-weight:700; color:var(--red); margin-bottom:4px; }
.mh-version-partial-list{
  list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px;
  font-family:var(--mono); font-size:10.5px; color:var(--ink-soft);
}

/* ── Version diff (centre pane takeover, Phase B P4) ──────────────────────── */
.mh-version-diff{ display:flex; flex-direction:column; height:100%; min-height:0; }

/* Morandi four-state palette — muted, grayed diff colours scoped to the diff
   pane only, so the shell's semantic --green / --red / --amber / --indigo stay
   untouched. Each state carries a readable ink (badge text, strike-through,
   word ins/del) plus a soft grayed fill. Non-colour cues (line-through, +/−
   badges) still carry the semantics, so the low-contrast morandi tones remain
   colour-blind safe. Light and dark are tuned separately: dark drops the fills
   to low-luminance chips and lifts the ink, keeping the grayed feel. */
.mh-editor-shell[data-theme='light'] .mh-version-diff{
  --diff-add:#4f7259;  --diff-add-bg:#e8efe9;
  --diff-del:#a86560;  --diff-del-bg:#f4e7e6;
  --diff-chg:#9a7a4c;  --diff-chg-bg:#f2ebdd;
  --diff-move:#6e6796; --diff-move-bg:#ebe9f3;
}
.mh-editor-shell[data-theme='dark'] .mh-version-diff{
  --diff-add:#8ebf9c;  --diff-add-bg:#25352c;
  --diff-del:#d99b93;  --diff-del-bg:#3a2825;
  --diff-chg:#cdb184;  --diff-chg-bg:#352c1e;
  --diff-move:#a9a1cf; --diff-move-bg:#2d2842;
}
.mh-diff-topbar{
  display:flex; align-items:center; gap:12px; flex-shrink:0;
  padding:10px 16px; border-bottom:1px solid var(--surface-border);
}
.mh-diff-back{
  font-family:var(--sans); font-size:12px; font-weight:600; cursor:pointer;
  padding:5px 12px; border-radius:var(--radius-sm);
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-diff-back:hover{ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--indigo); }
.mh-diff-title{ display:flex; align-items:center; gap:8px; min-width:0; font-size:13px; }
.mh-diff-side{ font-weight:600; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mh-diff-side.to{ color:var(--indigo-deep); }
.mh-diff-arrow{ flex-shrink:0; color:var(--ink-faint); }
.mh-diff-scroll{ flex:1; min-height:0; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:14px; }
.mh-diff-state{ margin:auto; font-size:13px; color:var(--ink-faint); font-style:italic; }
.mh-diff-state.error{ color:var(--red); font-style:normal; }
/* Collapsible group heads (scene-set aggregation + per-scene sections). */
.mh-diff-group-head{
  display:flex; align-items:center; gap:8px; width:100%; text-align:left;
  background:none; border:none; cursor:pointer; padding:2px 0; min-width:0;
}
.mh-diff-caret{
  flex-shrink:0; font-size:9px; color:var(--ink-faint);
  transition:transform 0.12s ease; transform:rotate(0deg);
}
.mh-diff-caret.open{ transform:rotate(90deg); }

/* Scene-set add/remove aggregation ("Added N scenes" → expandable list). */
.mh-diff-sceneset{ display:flex; flex-direction:column; gap:6px; }
.mh-diff-group-label{
  font-size:12.5px; font-weight:600; color:var(--ink); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;
}
.mh-diff-sceneset-list{
  list-style:none; margin:0; padding:0 0 0 18px; display:flex; flex-direction:column; gap:3px;
}
.mh-diff-sceneset-item{
  display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink);
  padding:2px 6px; border-radius:5px; min-width:0;
}
.mh-diff-sceneset-item.jumpable{ cursor:pointer; }
.mh-diff-sceneset-item.jumpable:hover{ background:var(--surface-2); }
.mh-diff-scene-name{ font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

/* Per-scene change section. */
.mh-diff-scene{ display:flex; flex-direction:column; gap:8px; }
.mh-diff-scene-head{ align-items:baseline; }
.mh-diff-scene-heading{
  margin:0; font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:0.04em;
  text-transform:uppercase; color:var(--ink-faint); overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;
}
.mh-diff-scene-count{
  flex-shrink:0; font-family:var(--mono); font-size:10px; font-weight:700;
  color:var(--ink-faint); background:var(--surface-2); border-radius:999px; padding:1px 7px;
}
.mh-diff-changes{ list-style:none; margin:0; padding:0 0 0 18px; display:flex; flex-direction:column; gap:8px; }
.mh-diff-change{
  display:flex; flex-direction:column; gap:4px; padding:5px 8px; border-radius:6px;
  border:1px solid transparent;
}
.mh-diff-change.jumpable{ cursor:pointer; }
.mh-diff-change.jumpable:hover{ border-color:var(--surface-border); background:var(--surface-2); }
.mh-diff-change-head{ display:flex; align-items:center; gap:8px; }
.mh-diff-badge{
  flex-shrink:0; font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:0.03em;
  text-transform:uppercase; padding:3px 7px; border-radius:999px;
}
.mh-diff-badge.added{ background:var(--diff-add-bg); color:var(--diff-add); }
.mh-diff-badge.removed{ background:var(--diff-del-bg); color:var(--diff-del); }
.mh-diff-badge.changed{ background:var(--diff-chg-bg); color:var(--diff-chg); }
.mh-diff-badge.moved{ background:var(--diff-move-bg); color:var(--diff-move); }

/* Author chip — monochrome, quiet; sits after the kind badge. */
.mh-diff-author{
  flex-shrink:0; font-family:var(--sans); font-size:10.5px; font-weight:600;
  color:var(--ink-soft); background:var(--surface-2); border-radius:999px; padding:2px 8px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:120px;
}

/* Change bodies. Equal word segments stay readable; only del/ins are tinted. */
.mh-diff-texts{ min-width:0; display:flex; flex-direction:column; gap:4px; }
.mh-diff-text{
  font-size:13px; line-height:1.5; padding:3px 8px; border-radius:5px;
  word-break:break-word; color:var(--ink);
}
.mh-diff-text.before{ background:var(--diff-del-bg); }
.mh-diff-text.after{ background:var(--diff-add-bg); }
/* Whole-line removed / added keep their full colour cue. */
.mh-diff-change.removed .mh-diff-text.before{ color:var(--diff-del); text-decoration:line-through; }
.mh-diff-change.added .mh-diff-text.after{ color:var(--diff-add); }
.mh-diff-change.moved .mh-diff-text.after{ color:var(--diff-move); background:var(--diff-move-bg); }
/* Word-level inline highlights (Cursor style). */
.mh-w-eq{ color:var(--ink-soft); }
.mh-w-del{ color:var(--diff-del); text-decoration:line-through; font-weight:600; }
.mh-w-ins{ color:var(--diff-add); font-weight:600; }

/* Jump-to flash pulse on the live sheet target. */
@keyframes mhDiffJumpFlash{
  0%{ box-shadow:0 0 0 3px var(--indigo-soft); }
  100%{ box-shadow:0 0 0 3px transparent; }
}
.mh-diff-jump-flash{ animation:mhDiffJumpFlash 1.4s ease-out; border-radius:4px; }

/* ── Beats view ─────────────────────────────────────────────────────────── */
.mh-beats-view{ height:100%; overflow-y:auto; padding:20px 24px; }
.mh-beats-head{
  display:flex; align-items:center; justify-content:space-between; margin-bottom:14px;
}
.mh-beats-title{ font-size:15px; font-weight:800; color:var(--ink); letter-spacing:-0.01em; }
.mh-beats-add-btn{
  font-family:var(--sans); font-size:12.5px; font-weight:700; padding:7px 14px;
  border-radius:var(--radius-sm); cursor:pointer; background:var(--indigo);
  color:var(--accent-on); border:none;
}
.mh-beats-add-btn:hover{ background:var(--indigo-deep); }
.mh-beats-list{ display:flex; flex-direction:column; }
.mh-beat-row{ position:relative; }
.mh-beats-empty{
  margin:auto; max-width:440px; text-align:center; display:flex; flex-direction:column;
  align-items:center; gap:10px; padding:48px 24px;
}
.mh-beats-empty-title{ font-size:16px; font-weight:800; color:var(--ink); }
.mh-beats-empty-sub{ font-size:13px; line-height:1.6; color:var(--ink-faint); margin:0 0 2px; }
/* Quiet "or" rule between the template cards and the ghost Add — the loud action
   is picking a methodology, not adding a blank beat. */
.mh-beats-empty-or{
  display:flex; align-items:center; gap:12px; width:100%; max-width:340px; margin:2px 0;
  color:var(--ink-faint); font-size:11px; font-weight:700; letter-spacing:.06em;
  text-transform:uppercase;
}
.mh-beats-empty-or::before, .mh-beats-empty-or::after{
  content:''; flex:1; height:1px; background:var(--hairline);
}
.mh-beats-add-ghost{
  font-family:var(--sans); font-size:12.5px; font-weight:700; padding:7px 18px;
  border-radius:999px; cursor:pointer; background:transparent; color:var(--ink);
  border:1px solid var(--surface-border);
}
.mh-beats-add-ghost:hover{ border-color:var(--emph-ink-border); background:var(--surface-2); }
/* Empty-state template cards read tighter than the wizard's (leading affordance,
   not a full picker). */
.mh-tpl-empty-cards .mh-tpl-empty-card{ padding:10px 12px; gap:2px; }

.mh-beat-card{
  position:relative; border:1px solid var(--hairline); border-radius:var(--radius-md);
  background:var(--surface); padding:10px 12px; margin:5px 0;
  display:flex; flex-direction:column; gap:8px; overflow:hidden;
}
.mh-beat-color-bar{
  position:absolute; left:0; top:0; bottom:0; width:4px; border-radius:var(--radius-md) 0 0 var(--radius-md);
}
.mh-beat-duration-chip{
  flex-shrink:0; font-size:11px; font-weight:700; color:var(--accent-text);
  background:var(--accent-soft); border:1px solid var(--accent-border);
  border-radius:999px; padding:2px 8px; line-height:1.4;
}
.mh-beat-arrange{
  display:flex; flex-wrap:wrap; align-items:center; gap:12px;
}
.mh-beat-duration-field{ display:flex; align-items:center; gap:6px; }
.mh-beat-field-label{ font-size:11.5px; font-weight:600; color:var(--ink-faint); }
.mh-beat-duration-input{
  width:72px; font-family:var(--sans); font-size:12px; color:var(--ink);
  background:var(--surface-2); border:1px solid var(--hairline);
  border-radius:var(--radius-sm); padding:4px 7px;
}
.mh-beat-duration-input:focus{ outline:none; border-color:var(--indigo); }
.mh-beat-color-swatches{ display:flex; align-items:center; gap:5px; flex-wrap:wrap; }
.mh-beat-color-swatch{
  width:18px; height:18px; border-radius:50%; border:1px solid var(--hairline);
  cursor:pointer; padding:0;
}
.mh-beat-color-swatch.selected{ box-shadow:0 0 0 2px var(--surface), 0 0 0 3.5px var(--ink-soft); }
.mh-beat-color-clear{
  font-size:12px; line-height:1; color:var(--ink-faint); background:transparent;
  border:1px solid var(--hairline); border-radius:50%; width:18px; height:18px; cursor:pointer;
}
.mh-beat-color-clear:hover{ color:var(--red); }
.mh-beat-card.dragging{ opacity:0.55; }
.mh-beat-card-head{ display:flex; align-items:center; gap:8px; }
.mh-beat-drag-handle{
  cursor:grab; color:var(--ink-faint); font-size:13px; line-height:1; letter-spacing:-2px;
  user-select:none; padding:2px;
}
.mh-beat-num{
  flex-shrink:0; width:22px; height:22px; border-radius:6px; background:var(--surface-2);
  color:var(--ink-soft); font-size:12px; font-weight:700;
  display:flex; align-items:center; justify-content:center;
}
.mh-beat-title{
  flex:1; min-width:0; font-family:var(--sans); font-size:13.5px; font-weight:600;
  color:var(--ink); background:transparent; border:1px solid transparent;
  border-radius:var(--radius-sm); padding:5px 7px;
}
.mh-beat-title:hover{ border-color:var(--hairline); }
.mh-beat-title:focus{ outline:none; border-color:var(--indigo); background:var(--surface-2); }
.mh-beat-notes-toggle{
  flex-shrink:0; font-size:11.5px; font-weight:600; color:var(--ink-faint);
  background:transparent; border:1px solid var(--hairline); border-radius:var(--radius-sm);
  padding:4px 8px; cursor:pointer;
}
.mh-beat-notes-toggle[aria-expanded="true"]{ color:var(--ink); border-color:var(--emph-ink-border); background:var(--sel-ink-bg); }
.mh-beat-delete{
  flex-shrink:0; font-size:14px; line-height:1; color:var(--ink-faint); background:transparent;
  border:1px solid transparent; border-radius:var(--radius-sm); padding:3px 8px; cursor:pointer;
}
.mh-beat-delete:hover{ color:var(--red); }
.mh-beat-delete.confirming{ color:var(--accent-on); background:var(--red); font-size:11.5px; font-weight:700; }
.mh-beat-summary{
  width:100%; resize:vertical; font-family:var(--sans); font-size:12.5px; line-height:1.6;
  color:var(--ink-soft); background:var(--surface-2); border:1px solid var(--hairline);
  border-radius:var(--radius-sm); padding:6px 8px;
}
.mh-beat-summary:focus{ outline:none; border-color:var(--indigo); }
.mh-beat-scenes{ display:flex; flex-wrap:wrap; align-items:center; gap:6px; }
.mh-beat-chip{
  display:inline-flex; align-items:center; gap:2px; background:var(--chip-ink-bg);
  border-radius:999px; padding:0 2px 0 0; max-width:100%;
}
.mh-beat-chip-label{
  font-size:11.5px; font-weight:600; color:var(--chip-ink-fg); background:transparent;
  border:none; border-radius:999px; padding:3px 4px 3px 10px; cursor:pointer;
  max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.mh-beat-chip-label:disabled{ cursor:default; color:var(--ink-faint); }
.mh-beat-chip-x{
  font-size:12px; line-height:1; color:var(--chip-ink-fg); background:transparent; border:none;
  border-radius:999px; width:16px; height:16px; cursor:pointer; opacity:0.65;
}
.mh-beat-chip-x:hover{ opacity:1; }
.mh-beat-link-select{
  font-family:var(--sans); font-size:11.5px; color:var(--ink-faint); background:var(--surface-2);
  border:1px solid var(--hairline); border-radius:999px; padding:3px 8px; cursor:pointer;
}

/* ── Beats pane (M2): sub-view toggle + Arrangement timeline ─────────────── */
/* flex:1 + min-width:0 (NOT height:100%): the pane is a flex item of
   .mh-page-frame — without the min-width floor release the timeline canvas's
   min-content width blows the column past the right island and cards paint over
   the writing panel (same class of bug as the sheet's minmax(0,1fr) fix). */
.mh-beats-pane{ flex:1; min-width:0; display:flex; flex-direction:column; min-height:0; }
/* The Arrangement/List sub-view switch now lives in the shell top-bar tab slot
   (shared .mh-doc-tabs), so the Beats pane no longer draws its own segmented row
   — the old .mh-beats-subview / .mh-beats-seg rules were removed with it.
   "+ Add beat" moved into the Arrangement toolbar's right group (see
   .mh-arr-topbar-right); its ink pill is sized to sit level with the zoom pill. */
.mh-beats-add-ink{
  display:inline-flex; align-items:center; height:34px; padding:0 16px;
  font-family:var(--sans); font-size:12px; font-weight:700; color:var(--pill-ink-on);
  background:var(--pill-ink-bg); border:none; border-radius:999px;
  cursor:pointer; line-height:1; white-space:nowrap;
}
.mh-beats-add-ink:hover{ opacity:0.88; }
.mh-beats-pane > .mh-beats-view,
.mh-beats-pane > .mh-arr-root{ flex:1; min-height:0; }

.mh-arr-root{ position:relative; display:flex; flex-direction:column; overflow:hidden; }
.mh-arr-root > .mh-beats-empty{ margin:auto; }
.mh-arr-body{ flex:1; min-height:0; display:flex; }

/* Left rail — laper-light: no card blocks, just grip + number + title. The
   selected beat reads as bold ink, not a filled row. */
.mh-arr-list{
  flex:0 0 178px; overflow-y:auto; padding:14px 6px;
  border-right:1px solid color-mix(in srgb, var(--surface-border) 60%, transparent);
  display:flex; flex-direction:column; gap:1px;
}
.mh-arr-list-item{
  display:flex; align-items:center; gap:7px; text-align:left; cursor:pointer;
  background:none; border:none; border-radius:8px; padding:5px 8px;
  color:var(--ink-faint); font-family:var(--sans);
}
.mh-arr-list-item:hover{ color:var(--ink-soft); background:var(--hover-ink-bg); }
.mh-arr-list-item.selected{ color:var(--ink); }
.mh-arr-list-grip{ flex-shrink:0; font-size:12px; line-height:1; opacity:0.45; }
.mh-arr-list-num{ flex-shrink:0; min-width:16px; font-size:11px; font-weight:700; font-variant-numeric:tabular-nums; }
.mh-arr-list-title{ font-size:12.5px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.mh-arr-list-item.selected .mh-arr-list-title{ font-weight:800; }

.mh-arr-stage{ position:relative; flex:1; min-width:0; display:flex; }
.mh-arr-timeline-scroll{ flex:1; min-width:0; overflow:auto; position:relative; }
.mh-arr-canvas{ position:relative; min-width:100%; }

/* Ruler: a thin baseline with film-apostrophe labels; majors drop an 8px tick,
   minors a 4px hairline. */
.mh-arr-ruler{
  position:sticky; top:0; height:30px; z-index:3;
  background:var(--surface); border-bottom:1px solid var(--surface-border);
}
.mh-arr-tick{ position:absolute; bottom:0; width:1px; height:4px; background:var(--hairline); }
.mh-arr-tick.major{ height:8px; background:var(--ink-faint); }
.mh-arr-tick-label{
  position:absolute; bottom:11px; left:3px; white-space:nowrap;
  font-size:10px; font-weight:700; color:var(--ink-faint); font-variant-numeric:tabular-nums;
}

/* Lanes: the dot-grid canvas the cards float on. */
.mh-arr-lanes{
  position:relative;
  background-image:radial-gradient(circle, color-mix(in srgb, var(--ink) 8%, transparent) 1.4px, transparent 1.4px);
  background-size:24px 24px; background-position:12px 12px;
}

/* Tall white beat card: top color bar (color data's new home) + title + 2-line
   summary + footer (neutral duration chip / Edit Beat ghost). */
.mh-arr-card{
  position:absolute; box-sizing:border-box; cursor:grab; user-select:none; touch-action:none;
  overflow:hidden; display:flex; flex-direction:column; gap:5px; padding:12px 13px 10px;
  /* laper minimal but LEGIBLE: white card on the near-white canvas needs a
     visible border + a whisper of resting shadow, or it disappears. */
  background:var(--surface); border:1px solid var(--surface-border); border-radius:10px;
  box-shadow:0 1px 3px rgba(28,27,32,0.07);
  transition:box-shadow 0.15s ease, border-color 0.15s ease;
}
.mh-arr-card:hover{ box-shadow:var(--shadow-island); }
.mh-arr-card.selected{ border-color:var(--emph-ink-border); }
.mh-arr-card.dragging{ cursor:grabbing; z-index:4; box-shadow:var(--shadow-float); transform:none; }
.mh-arr-card.flash{ animation:mh-arr-flash 0.7s ease; }
@keyframes mh-arr-flash{
  0%,100%{ box-shadow:var(--shadow-island); }
  30%{ box-shadow:0 0 0 3px var(--accent-soft), var(--shadow-float); }
}
.mh-arr-card-bar{ flex-shrink:0; width:24px; height:3px; border-radius:2px; background:var(--ink-faint); }
.mh-arr-card-title{
  font-size:13.5px; font-weight:700; line-height:1.3; color:var(--ink);
  overflow:hidden; text-overflow:ellipsis;
  display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
}
.mh-arr-card-summary{
  flex:1; min-height:0; font-size:12px; line-height:1.5; color:var(--ink-faint);
  overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
}
.mh-arr-card-foot{ display:flex; align-items:center; justify-content:space-between; gap:8px; margin-top:auto; }
.mh-arr-card-chip{
  flex-shrink:0; font-size:10.5px; font-weight:700; color:var(--chip-ink-fg);
  background:var(--chip-ink-bg); border-radius:999px; padding:2px 8px; font-variant-numeric:tabular-nums;
}
.mh-arr-card-edit{
  flex-shrink:0; font-size:10.5px; font-weight:600; color:var(--ink-soft); font-family:var(--sans);
  background:none; border:1px solid var(--hairline); border-radius:999px; padding:2px 9px; cursor:pointer;
}
.mh-arr-card-edit:hover{ color:var(--ink); border-color:var(--emph-ink-border); background:var(--hover-ink-bg); }
.mh-arr-card-resize{ position:absolute; right:0; top:0; bottom:0; width:10px; cursor:ew-resize; touch-action:none; }
.mh-arr-card-resize:hover{ background:linear-gradient(to right, transparent, var(--accent-soft)); }

/* ── M4 memo-pin rail ─────────────────────────────────────────────────────
   A second ruler line below the cards where time-anchored inspiration memos
   hang as draggable pins with relaxed connector curves + floating cards. */
.mh-memo-rail{ position:relative; margin-top:8px; padding-top:6px; border-top:1px dashed var(--surface-border); }
.mh-memo-ruler-line{ position:absolute; left:0; right:0; top:15px; height:1px; background:var(--hairline); }
.mh-memo-tick{ position:absolute; top:11px; width:1px; height:9px; background:var(--surface-border); }
.mh-memo-hitzone{ position:absolute; left:0; right:0; top:0; z-index:1; cursor:copy; }
/* Hover "+" snapped to the grid — laper's add affordance in accent green. */
.mh-memo-add{
  position:absolute; z-index:3; transform:translate(-50%,-50%);
  width:18px; height:18px; border-radius:999px;
  display:flex; align-items:center; justify-content:center;
  font-size:14px; font-weight:700; line-height:1; color:#fff;
  background:var(--positive, #3fae6a); box-shadow:var(--shadow-island);
  pointer-events:none; /* pure hover hint; the hitzone owns the click */
}
.mh-memo-links{ position:absolute; left:0; top:0; z-index:1; pointer-events:none; overflow:visible; }
.mh-memo-link{ fill:none; stroke-width:1.5; opacity:0.55; }
.mh-memo-dot{
  position:absolute; z-index:4; transform:translate(-50%,-50%);
  width:11px; height:11px; border-radius:999px; padding:0; cursor:grab;
  border:2px solid var(--surface-1, #fff); box-shadow:var(--shadow-island);
  touch-action:none;
}
.mh-memo-dot:hover{ filter:brightness(1.05); }
.mh-memo-dot.dragging{ cursor:grabbing; z-index:6; transform:translate(-50%,-50%) scale(1.15); }
.mh-memo-card{
  position:absolute; z-index:2; box-sizing:border-box;
  background:var(--surface-1, #fff); border:1px solid var(--hairline); border-radius:10px;
  padding:8px 10px; box-shadow:var(--shadow-island);
}
.mh-memo-card-head{ display:flex; align-items:center; gap:8px; }
.mh-memo-card-chip{
  font-size:10.5px; font-weight:700; color:var(--chip-ink-fg); background:var(--chip-ink-bg);
  border-radius:999px; padding:2px 8px; font-variant-numeric:tabular-nums;
}
.mh-memo-card-edit{
  margin-left:auto; font-size:10.5px; font-weight:600; color:var(--ink-soft);
  background:none; border:none; cursor:pointer; padding:2px 2px;
}
.mh-memo-card-edit:hover{ color:var(--ink); text-decoration:underline; }
.mh-memo-card-thumb{
  display:block; width:100%; max-height:88px; object-fit:cover;
  border-radius:6px; margin-top:6px;
}
.mh-memo-card-body{
  margin-top:6px; font-size:12px; line-height:1.5; color:var(--ink-faint);
  overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
}

/* Quick-capture popover — laper post-style composer. */
.mh-memo-quick{
  position:absolute; z-index:20; top:30px; width:272px; box-sizing:border-box;
  background:var(--surface-1, #fff); border:1px solid var(--emph-ink-border); border-radius:12px;
  padding:10px; box-shadow:var(--shadow-float);
}
.mh-memo-quick-head{ display:flex; align-items:center; gap:8px; }
.mh-memo-quick-chip{
  font-size:11px; font-weight:700; color:var(--chip-ink-fg); background:var(--chip-ink-bg);
  border-radius:999px; padding:2px 9px; font-variant-numeric:tabular-nums;
}
.mh-memo-quick-x{ margin-left:auto; color:var(--ink-soft); background:none; border:none; cursor:pointer; padding:2px; display:flex; }
.mh-memo-quick-x:hover{ color:var(--ink); }
.mh-memo-quick-body{
  width:100%; box-sizing:border-box; margin-top:8px; resize:vertical; min-height:64px;
  font-family:var(--sans); font-size:13px; line-height:1.5; color:var(--ink);
  background:var(--surface-2, transparent); border:1px solid var(--hairline); border-radius:8px; padding:8px;
}
.mh-memo-quick-body:focus{ outline:none; border-color:var(--emph-ink-border); }
.mh-memo-quick-thumbs{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.mh-memo-quick-thumb-wrap{ position:relative; display:inline-flex; }
.mh-memo-quick-thumb{ width:48px; height:48px; object-fit:cover; border-radius:6px; border:1px solid var(--hairline); }
.mh-memo-quick-thumb-x{
  position:absolute; top:-6px; right:-6px; width:16px; height:16px; border-radius:999px;
  display:flex; align-items:center; justify-content:center; cursor:pointer;
  color:#fff; background:var(--ink); border:none;
}
.mh-memo-quick-foot{ display:flex; align-items:center; gap:8px; margin-top:10px; }
.mh-memo-quick-foot-spacer{ flex:1; }
.mh-memo-quick-attach{
  display:inline-flex; align-items:center; gap:3px; font-size:11px; font-weight:600;
  color:var(--ink-soft); background:none; border:1px solid var(--hairline); border-radius:999px;
  padding:4px 9px; cursor:pointer;
}
.mh-memo-quick-attach:hover:not(:disabled){ color:var(--ink); border-color:var(--emph-ink-border); }
.mh-memo-quick-attach:disabled{ opacity:0.4; cursor:not-allowed; }
.mh-memo-quick-ghost{
  font-size:11px; font-weight:600; color:var(--ink-soft); background:none; border:none;
  cursor:pointer; padding:4px 4px; display:inline-flex; align-items:center;
}
.mh-memo-quick-ghost:hover{ color:var(--ink); }
.mh-memo-quick-publish{
  font-size:12px; font-weight:700; color:#fff; background:var(--accent-strong, var(--indigo));
  border:none; border-radius:999px; padding:5px 14px; cursor:pointer;
}
.mh-memo-quick-publish:disabled{ opacity:0.45; cursor:not-allowed; }

/* Tools pill — top-right, laper-style with text labels (Fit / Zoom out / in). */
/* NLE drag guide — full-height edge line with a live time chip. */
.mh-arr-guide{
  position:absolute; top:0; bottom:0; width:1px; z-index:5; pointer-events:none;
  background:var(--indigo); opacity:0.75;
}
.mh-arr-guide-chip{
  position:absolute; top:2px; left:4px; font-size:10px; font-weight:700; line-height:1;
  color:#fff; background:var(--indigo); border-radius:5px; padding:3px 6px;
  font-variant-numeric:tabular-nums; white-space:nowrap;
}

/* In-flow toolbar row (absolute over the canvas covered the ruler labels).
   Left group = length + methodology tools, right group = zoom. */
.mh-arr-topbar{ display:flex; justify-content:space-between; align-items:center; gap:8px; padding:0 6px 8px; flex:0 0 auto; }
/* Right cluster of the Arrangement toolbar: the ink "+ Add beat" pill sits just
   left of the Fit/zoom pill, both pushed right by the topbar's space-between. */
.mh-arr-topbar-right{ display:flex; align-items:center; gap:8px; }
.mh-arr-tools{
  display:flex; align-items:center; gap:2px;
  background:var(--surface); border:1px solid var(--surface-border); border-radius:10px;
  padding:4px; box-shadow:var(--shadow-island);
}
.mh-arr-tool-btn{
  display:flex; align-items:center; gap:4px; height:26px; padding:0 10px; line-height:1;
  border:none; background:none; border-radius:7px; cursor:pointer; font-family:var(--sans);
  font-size:11px; font-weight:700; color:var(--ink-soft); white-space:nowrap;
}
.mh-arr-tool-btn:hover{ background:var(--hover-ink-bg); color:var(--ink); }
.mh-arr-tool-sep{ width:1px; height:16px; background:var(--surface-border); margin:0 2px; }

/* Unarranged tray — dashed ghost cards below the canvas. */
.mh-arr-tray{
  flex:0 0 auto; border-top:1px solid var(--surface-border); padding:10px 24px 14px;
  display:flex; align-items:flex-start; gap:12px; overflow-x:auto;
}
.mh-arr-tray-label{
  flex-shrink:0; padding-top:9px; font-size:10.5px; font-weight:700; letter-spacing:0.05em;
  text-transform:uppercase; color:var(--ink-faint);
}
.mh-arr-tray-cards{ display:flex; gap:8px; flex-wrap:wrap; }
.mh-arr-tray-card{
  display:flex; align-items:center; gap:8px; overflow:hidden;
  background:transparent; border:1px dashed var(--surface-border); border-radius:var(--radius-sm);
  padding:7px 10px 7px 11px;
}
.mh-arr-tray-bar{ flex-shrink:0; width:10px; height:10px; border-radius:50%; background:var(--ink-faint); }
.mh-arr-tray-title{
  font-size:12px; font-weight:600; color:var(--ink-soft); max-width:150px;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.mh-arr-tray-place{
  flex-shrink:0; font-size:11px; font-weight:700; color:var(--accent-text);
  background:var(--accent-soft); border:1px solid var(--accent-border);
  border-radius:999px; padding:2px 10px; cursor:pointer; font-family:var(--sans);
}
.mh-arr-tray-place:hover{ filter:brightness(1.04); }

/* Edit Beat modal — centred overlay in the editor-shell ink chrome. */
.mh-arr-modal-overlay{
  position:fixed; inset:0; z-index:60; display:flex; align-items:center; justify-content:center;
  padding:24px; background:color-mix(in srgb, #000 55%, transparent);
}
.mh-arr-modal{
  width:100%; max-width:440px; max-height:88vh; overflow-y:auto; box-sizing:border-box;
  display:flex; flex-direction:column; gap:14px; padding:18px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-lg); box-shadow:var(--shadow-float);
}
.mh-arr-modal-head{ display:flex; align-items:center; justify-content:space-between; }
.mh-arr-modal-title{ font-size:14px; font-weight:800; color:var(--ink); }
.mh-arr-modal-x{
  background:none; border:none; font-size:18px; line-height:1; color:var(--ink-faint);
  cursor:pointer; padding:2px 7px; border-radius:6px;
}
.mh-arr-modal-x:hover{ color:var(--ink); background:var(--hover-ink-bg); }
.mh-arr-field{ display:flex; flex-direction:column; gap:6px; }
.mh-arr-field-label{
  font-size:11px; font-weight:700; letter-spacing:0.03em; text-transform:uppercase; color:var(--ink-faint);
}
.mh-arr-input, .mh-arr-textarea{
  width:100%; box-sizing:border-box; font-family:var(--sans); font-size:13px; color:var(--ink);
  background:var(--surface-2); border:1px solid var(--hairline); border-radius:var(--radius-sm); padding:8px 10px;
}
.mh-arr-input.narrow{ width:120px; }
.mh-arr-textarea{ resize:vertical; line-height:1.5; }
.mh-arr-input:focus, .mh-arr-textarea:focus{ outline:none; border-color:var(--indigo); }
.mh-arr-modal-foot{ display:flex; justify-content:flex-end; gap:8px; margin-top:2px; }
.mh-arr-modal-btn{
  font-family:var(--sans); font-size:12.5px; font-weight:700; border-radius:var(--radius-sm);
  padding:7px 16px; cursor:pointer; white-space:nowrap; flex-shrink:0;
}
.mh-arr-modal-btn.ghost{ background:none; border:1px solid var(--hairline); color:var(--ink-soft); }
.mh-arr-modal-btn.ghost:hover{ color:var(--ink); background:var(--hover-ink-bg); }
.mh-arr-modal-btn.primary{ background:var(--indigo); border:none; color:var(--accent-on); }
.mh-arr-modal-btn.primary:hover{ background:var(--indigo-deep); }
.mh-arr-modal-btn:disabled{ opacity:0.45; cursor:not-allowed; }

/* ── Beats M3: target-length control, template wizard, methodology guide ─── */
/* Target length control — a tool-btn trigger + a small popover of presets. */
.mh-arr-len{ position:relative; }
.mh-arr-len-btn.unset{ color:var(--ink-faint); }
.mh-arr-len-icon{ font-size:11px; opacity:0.7; }
.mh-arr-len-pop{
  position:absolute; top:calc(100% + 6px); left:0; z-index:20; width:248px;
  display:flex; flex-direction:column; gap:10px; padding:12px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); box-shadow:var(--shadow-float);
}
.mh-arr-len-presets{ display:flex; flex-wrap:wrap; gap:6px; }
.mh-arr-len-preset{
  font-family:var(--sans); font-size:12px; font-weight:700; color:var(--ink-soft);
  background:var(--surface-2); border:1px solid var(--hairline); border-radius:999px;
  padding:4px 12px; cursor:pointer; font-variant-numeric:tabular-nums;
}
.mh-arr-len-preset:hover{ color:var(--ink); border-color:var(--emph-ink-border); }
.mh-arr-len-preset.selected{ color:var(--accent-on); background:var(--indigo); border-color:transparent; }
.mh-arr-len-custom{ display:flex; align-items:center; gap:6px; }
/* min-width:0 releases the input's min-content floor — without it the fixed-
   width popover shoves the Set button past its own right edge (超界). */
.mh-arr-len-custom .mh-arr-input.narrow{ flex:1; width:auto; min-width:0; }

/* Template wizard — reuses the arr-modal shell; wider for the three cards. */
.mh-tpl-modal{ max-width:520px; }
.mh-tpl-cards{ display:flex; flex-direction:column; gap:10px; }
.mh-tpl-card, .mh-tpl-empty-card{
  display:flex; flex-direction:column; gap:4px; text-align:left; cursor:pointer;
  padding:12px 14px; border:1px solid var(--surface-border); border-radius:var(--radius-md);
  background:var(--surface-2); font-family:var(--sans);
}
.mh-tpl-card:hover, .mh-tpl-empty-card:hover{ border-color:var(--emph-ink-border); }
.mh-tpl-card.selected{ border-color:var(--indigo); box-shadow:0 0 0 1px var(--indigo); }
.mh-tpl-card-name{ font-size:13.5px; font-weight:800; color:var(--ink); }
.mh-tpl-card-desc{ font-size:12px; line-height:1.5; color:var(--ink-faint); }
.mh-tpl-card-count{ font-size:11px; font-weight:700; color:var(--accent-text); }
.mh-tpl-hint{ margin:0; font-size:12px; line-height:1.5; color:var(--ink-faint); }
.mh-tpl-mode{
  display:flex; align-items:flex-start; gap:10px; cursor:pointer;
  padding:11px 13px; border:1px solid var(--surface-border); border-radius:var(--radius-md);
  background:var(--surface-2);
}
.mh-tpl-mode.selected{ border-color:var(--indigo); }
.mh-tpl-mode.danger.selected{ border-color:var(--red); }
.mh-tpl-mode input{ margin-top:3px; }
.mh-tpl-mode-body{ display:flex; flex-direction:column; gap:2px; }
.mh-tpl-mode-name{ font-size:13px; font-weight:700; color:var(--ink); }
.mh-tpl-mode.danger .mh-tpl-mode-name{ color:var(--red); }
.mh-tpl-mode-desc{ font-size:11.5px; line-height:1.45; color:var(--ink-faint); }
/* Empty-state template guide cards — leading affordance above Add Beat. */
.mh-tpl-empty-cards{ display:flex; flex-direction:column; gap:8px; width:100%; max-width:340px; }

/* ── Custom templates (M3.5) ─────────────────────────────────────────────── */
/* Each wizard card sits in a relative wrapper so a custom card can float its
   inline (two-click) delete button in the top-right corner without nesting a
   button inside the selection button. */
.mh-tpl-card-wrap{ position:relative; }
.mh-tpl-card-wrap .mh-tpl-card{ width:100%; }
.mh-tpl-card-delete{
  position:absolute; top:8px; right:8px; display:inline-flex; align-items:center;
  justify-content:center; min-width:22px; height:22px; padding:0 6px; cursor:pointer;
  border:1px solid var(--surface-border); border-radius:6px; background:var(--surface-1);
  color:var(--ink-faint); font-size:13px; font-weight:700; line-height:1;
}
.mh-tpl-card-delete:hover{ color:var(--red); border-color:var(--red); }
.mh-tpl-card-delete.confirm{
  color:var(--accent-on); background:var(--red); border-color:var(--red); font-size:11px;
}
/* Save-as-template dialog: a narrow name-only modal. */
.mh-tpl-save-modal{ max-width:420px; }
/* Empty-state "Your templates" section below the built-in guide cards. */
.mh-tpl-empty-custom{
  display:flex; flex-direction:column; gap:8px; width:100%; max-width:340px; margin-top:14px;
}
.mh-tpl-empty-custom-label{
  font-size:11px; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
  color:var(--ink-faint);
}
.mh-tpl-empty-custom-cards{ display:flex; flex-direction:column; gap:6px; }
.mh-tpl-empty-custom-card{
  display:flex; flex-direction:column; gap:3px; text-align:left; cursor:pointer;
  padding:9px 12px; border:1px solid var(--surface-border); border-radius:var(--radius-md);
  background:var(--surface-2); font-family:var(--sans);
}
.mh-tpl-empty-custom-card:hover{ border-color:var(--emph-ink-border); }


/* ===== FILM NUMBERING (合一终稿, 2026-07-11) =====
   Storyboard scene heads read S1/S2 (mono, neutral ink) and shot codes read 1A/1B
   (scene number + shot letter) instead of the old plain black number squares —
   the industry slate convention. Embedded (studio) mode now drops the editor's
   own rail entirely, so the old episode SWITCH menu that lived here is gone. */
.mh-sc-mark{
  font-family:var(--mono); font-size:11px; font-weight:700;
  color:var(--ink-soft); letter-spacing:.02em;
}
.mh-shot-no{
  font-family:var(--mono); font-size:9.5px; font-weight:700; color:var(--chip-ink-fg);
  background:var(--chip-ink-bg); border:1px solid var(--surface-border); border-radius:6px;
  padding:1.5px 6px; letter-spacing:.03em; flex-shrink:0;
}

/* ==================================================================
   SCREENPLAY CLEAN PAGE
   Strip the persistent chrome so the sheet reads like a printed
   script (a real screenplay page / laper.ai), not a form full of
   badges + ticks + cards. Every affordance — scene number, block
   number, drag handle — lives in the left margin and reveals on
   HOVER only. The resting page is just the formatted script on paper.
   ================================================================== */
/* No persistent colour ticks: the hover gutter (block number + 6-dot
   drag handle) is the sole per-line affordance. Removing the tick also
   pulls each line flush to the sheet's left margin (the Hollywood ch
   grid stays internally exact — every element loses the same 12px). */
.mh-el-tick{ display:none; }
/* Scene number → a faint margin number on hover, NOT a dark badge, so
   the slugline sits flush-left like a real heading. Aligned to the same
   margin column as the per-line block number. */
.mh-scene-headrow{ position:relative; }
.mh-scene-num-badge,
.mh-editor-shell[data-theme='dark'] .mh-scene-num-badge{
  position:absolute; left:-42px; top:3px; width:auto; height:auto; min-width:1.2em;
  background:none; color:var(--sheet-ink-soft); font-size:10.5px; font-weight:600;
  font-variant-numeric:tabular-nums; text-align:right; border-radius:0; padding:0;
  opacity:0; transition:opacity 0.12s ease; pointer-events:none;
}
.mh-scene-block:hover .mh-scene-num-badge,
.mh-scene-block:focus-within .mh-scene-num-badge{ opacity:1; }
/* Keep the per-line hover gutter aligned to the same margin column. */
.mh-el-gutter{ left:-42px; }
/* The keyboard cheat-sheet is chrome — hide it; the page stays clean. */
.mh-keyboard-hint{ display:none; }
/* Legacy chapter prose: a quiet inline notice, not a heavy orange card. */
.mh-chapter-fallback{
  background:transparent; border:none; border-left:2px solid var(--sheet-border);
  border-radius:0; padding:4px 0 4px 14px; margin:0 0 18px;
}
.mh-chapter-fallback-title{ font-size:12.5px; font-weight:600; color:var(--sheet-ink-soft); }
.mh-chapter-convert-btn{
  background:none; color:var(--indigo); border:1px solid var(--sheet-border);
  font-size:11px; padding:3px 9px;
}
.mh-chapter-convert-btn:hover:not(:disabled){ background:var(--indigo-soft); }
/* Cleaner paper — much less grain, closer to a printed white page. */
.mh-sheet::after{ opacity:0.02; }
/* A real script is monochrome: drop the decorative element colours so the
   page reads as black-on-paper (character cues, transitions, parens, subtitle
   all ink). The tick colour gutter is gone, so colour no longer carries type —
   the fixed column position does, exactly like a printed screenplay. */
/* A printed script distinguishes cues/transitions by COLUMN POSITION, not by
   weight — everything is uniform Courier. Drop the bold to match a real page. */
/* User verdict (2026-07-19): the character cue reads CENTERED on the page —
   not first-char-at-a-fixed-column. Centering replaces the 22ch indent as the
   cue's positional signal (long cues wrap centered too). */
.hw-character{
  color:var(--sheet-ink); font-weight:normal; letter-spacing:0;
  padding-left:0; text-align:center;
}
/* Character cue marker (laper parity + rail colour dot). A centred name reads as
   just-more-body-text (the user's report), so the cue floats as a soft grey pill
   (same --sheet-ink token as the heading chips) prefixed by the character's rail
   colour dot — the two cues a printed script carries by COLUMN position,
   restored on a page whose columns we deliberately flattened.

   The pill lives on the NodeViewContent's inner wrapper (.hw-character > *, the
   same child the paren rule styles), NOT on .hw-character itself, so the cue
   editable keeps its full 60ch column: the blank space to the RIGHT of the pill
   still just places a caret, and the cue-picker / character-cursor specs that
   probe that trailing space keep holding. Only a NON-empty cue gets the pill (an
   empty line has PM's trailing-break hack node — see the placeholder rules
   above — and should show only its type whisper + caret, not an empty chip). */
.hw-character:not(:has(br.ProseMirror-trailingBreak)) > *{
  display:inline-block; padding:2px 12px; border-radius:999px;
  background:color-mix(in srgb, var(--sheet-ink) 7%, transparent);
}
/* The dot rides the pill's ::before (a pseudo — never in the doc, so the caret
   can't land on it), gated on .cue-has-dot (set by the NodeView only when the
   name resolved to a rail colour) so an unknown cue wastes no leading space. */
.hw-character.cue-has-dot:not(:has(br.ProseMirror-trailingBreak)) > *::before{
  content:''; display:inline-block; width:7px; height:7px; border-radius:50%;
  margin-right:7px; vertical-align:middle; background:var(--cue-dot, transparent);
}
.hw-transition{ color:var(--sheet-ink); font-weight:normal; letter-spacing:0; }
/* Parenthetical stays ITALIC + soft even on the clean page (laper reference:
   "(dry…)" renders gray italic under the cue) — position AND voice carry it. */
.hw-paren{ color:var(--sheet-ink-soft); }
.hw-subtitle{ color:var(--sheet-ink); }
/* Comment on the clean page: laper renders it as a QUOTE block — a subtle left
   rule + gray italic. The base .hw-comment already carries the low-saturation
   ink bar; keep it here (only re-assert the soft text colour) rather than the
   old strip-to-borderless treatment. */
.hw-comment{ color:var(--sheet-ink-soft); }

/* ==================================================================
   EMPTY-STATE POLISH — the 16-point audit (empty scenes read like a
   real script page, not a form full of empty boxes + placeholders).
   ================================================================== */
/* #1,#2,#12: an empty line is NOT an input box. Kill the focus ring +
   rounded box so a focused empty 60ch line shows only the blinking
   caret, the way a real script line does. */
.mh-el-row.focused .mh-el-editable{ box-shadow:none; border-radius:0; }
/* No focus box AND no focus tint — a real script line shows only the blinking
   caret. (The user explicitly rejected the box; a tint still reads as a box.) */
.mh-el-row.focused{ background:none; }
/* #9: slug and action share ONE left margin (flush) — the heading container has
   no left padding, so its first token sits on the same column as the action text. */
/* #5: scene number + block gutter reveal on HOVER of that row/scene ONLY.
   focus-within kept them lit across the whole scene while typing — hide them
   again when focused-but-not-hovered (higher specificity wins over the reveal). */
.mh-el-row:focus-within:not(:hover) .mh-el-gutter{ opacity:0; pointer-events:none; }
.mh-scene-block:focus-within:not(:hover) .mh-scene-num-badge{ opacity:0; }
/* #10: align the scene number to the same margin column + size as the block
   number so the left rail reads as one tidy column (identical min-width to
   '.mh-el-num' — A5 one numbering style, not a wider chip column). */
.mh-scene-num-badge{ left:-42px; min-width:1.2em; text-align:right; font-size:10.5px; }
/* #4: the "按 Tab…" seed hint is a quiet whisper, not a headline. */
.mh-placeholder-line{ font-size:12px; opacity:0.45; }
/* #7,#13,#14: legacy chapter cards become a quiet unstarted-chapter list below
   the script — no border, no orange bar, no purple pill, empty-body hidden. */
.mh-chapter-fallback{ background:transparent; border:none; border-radius:0; padding:3px 0; margin:0 0 8px; }
.mh-chapter-fallback-head{ margin-bottom:0; }
.mh-chapter-fallback-title{ font-size:12.5px; font-weight:600; color:var(--sheet-ink-soft); }
.mh-chapter-fallback-body.empty{ display:none; }
.mh-chapter-convert-btn{ background:none; border:none; color:var(--sheet-ink-soft); text-decoration:underline; text-underline-offset:2px; font-size:11px; padding:0; }
.mh-chapter-convert-btn:hover:not(:disabled){ background:none; color:var(--indigo); }
/* #8,#16: calmer vertical rhythm between scenes. */
.mh-scene-block{ margin-bottom:20px; }

/* ==================================================================
   LAPER PARITY PASS — pixel-level alignment with the laper.ai page
   (user reference screenshots): warm paper, real screenplay margins,
   INT/EXT LOCATION - DAY/NIGHT chips for unset headings, per-type
   placeholders on the focused empty block, laper's kbd hint rows.
   ================================================================== */
/* Warm paper, not cool lavender-white (light theme only; dark keeps its ink). */
.mh-editor-shell[data-theme='light'] .mh-sheet{
  background:linear-gradient(180deg, #fdfcf8 0%, #faf8f1 100%);
  border-color:#eae7db;
}
/* A page, not a panel: this LAPER-PARITY override wins over the base rule for
   the vertical rhythm (roomier top/bottom) and the A4-ish min-height so even an
   empty script reads as a sheet. Horizontal margins + width now come from the
   shared --sheet-pad-* vars (base rule) so both rules stay in one Courier grid —
   only the vertical padding and the floor differ here. */
.mh-sheet{
  width:calc(60ch + var(--sheet-pad-l) + var(--sheet-pad-r) + 2 * var(--sheet-border-w));
  padding:56px var(--sheet-pad-r) 72px var(--sheet-pad-l);
  min-height:1040px;
}
/* Asian (华语) paper uses NARROWER, SYMMETRIC margins (8ch/8ch) than the
   Hollywood industry 15ch/10ch — the whole geometry (width + padding + the
   page-seam bleed, which derives its offsets from these same --sheet-pad-* vars)
   follows automatically because every consumer reads the vars, never a raw px.
   Custom properties inherit, so the seam widgets nested inside the sheet pick up
   the asian values too. Specificity (0,2,0) wins over the base .mh-sheet (0,1,0). */
.mh-sheet.asian{ --sheet-pad-l:8ch; --sheet-pad-r:8ch; }
/* Slug is uppercase REGULAR weight on a printed page (laper too) — position and
   case carry the meaning, not boldness. Weight/case live on .mh-scene-heading. */
/* Focused empty block whispers its element type (laper/Notion affordance) —
   driven purely by the data-el-type attr, so no component plumbing.
   TipTap mode (M2): ProseMirror always renders a trailing break element
   (class ProseMirror-trailingBreak) inside an empty textblock
   (prosemirror-view's addHackNode — unconditional, not something we can
   turn off) so the NodeView's content div is NEVER matched by :empty;
   :has(br...) (a DESCENDANT match, not a direct-child one — @tiptap/react's
   NodeViewContent interposes its own wrapper div between our data-el-id div
   and PM's actual contentDOM, so the break element sits two levels deep) is
   the equivalent signal for that engine. Legacy's contentEditable never
   contains that node, so this is purely additive — zero effect on the
   legacy path. */
.mh-el-editable:empty:focus::before,
.mh-el-editable:has(br.ProseMirror-trailingBreak):focus::before{ font-style:normal; opacity:0.4; }
.mh-el-editable[data-el-type='action']:empty:focus::before,
.mh-el-editable[data-el-type='action']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Action'; }
.mh-el-editable[data-el-type='character']:empty:focus::before,
.mh-el-editable[data-el-type='character']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Character'; }
.mh-el-editable[data-el-type='dialogue']:empty:focus::before,
.mh-el-editable[data-el-type='dialogue']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Dialogue'; }
.mh-el-editable[data-el-type='paren']:empty:focus::before,
.mh-el-editable[data-el-type='paren']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Parenthetical'; }
.mh-el-editable[data-el-type='transition']:empty:focus::before,
.mh-el-editable[data-el-type='transition']:has(br.ProseMirror-trailingBreak):focus::before{ content:'TRANS'; }
.mh-el-editable[data-el-type='comment']:empty:focus::before,
.mh-el-editable[data-el-type='comment']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Comment'; }
/* laper renders the empty-transition whisper as a ghost CHIP pinned to the
   right column (where the transition text will land), not plain prose. */
.mh-el-editable[data-el-type='transition']:empty::before,
.mh-el-editable[data-el-type='transition']:has(br.ProseMirror-trailingBreak)::before{
  display:inline-block; padding:0 7px; border-radius:4px;
  background:color-mix(in srgb, var(--sheet-ink) 7%, transparent);
  letter-spacing:0.08em; font-style:normal;
}
.mh-el-editable[data-el-type='subtitle']:empty:focus::before,
.mh-el-editable[data-el-type='subtitle']:has(br.ProseMirror-trailingBreak):focus::before{ content:'Subtitle'; }
/* TipTap mode: Chrome paints its UA focus ring (outline:auto, the blue ring)
   around a focused contenteditable — AND a separate ring segment around every
   contenteditable=false island inside it. Our per-block gutters are exactly
   such islands whose content is hover-revealed (opacity 0), so the rings read
   as a big blue frame plus a column of EMPTY blue boxes. Kill the outline on
   the per-scene ProseMirror container in every state (a focused
   contenteditable is ALWAYS :focus-visible in Chrome — see
   bug_contenteditable_focus_visible_ring); the caret + focused-row placeholder
   already convey focus. */
.mh-editor-shell .mh-tiptap-scene-editor,
.mh-editor-shell .mh-tiptap-scene-editor:focus,
.mh-editor-shell .mh-tiptap-scene-editor:focus-visible{ outline:none; }
/* laper's kbd hint rows — now rendered INSIDE the sheet top, only while the
   script has no typed text (EditorShell gates it), so a real script page stays
   pure. Overrides the earlier display:none chrome-kill. */
.mh-keyboard-hint{
  display:flex; flex-direction:column; align-items:flex-start; gap:9px;
  margin:0 0 36px; padding:0; border:none; background:none;
  font-family:var(--script-mono); font-size:12.5px;
  color:var(--sheet-ink-soft); opacity:0.8;
}
.mh-keyboard-hint kbd{
  background:color-mix(in srgb, var(--sheet-ink) 6%, transparent);
  border:1px solid var(--sheet-border); border-radius:4px;
  padding:0 7px; margin-right:9px;
  font-family:var(--mono); font-size:10.5px; color:var(--sheet-ink-soft);
}
.mh-keyboard-hint .sep{ display:none; }

/* ── Gutter refinement (user pass 2): 4 dots, lighter ink, true alignment ──
   The scene badge (top:3px), scene handle (top:1px) and element gutter each
   picked their own offsets — visibly misaligned rows. Unify: 2×2 dot grid on
   BOTH handles, one faint ink tint, scene badge+handle vertically centred on
   the heading row, and both columns right-aligned to the same x as the
   element gutter (num column ends -24px, handle ends -2px). */
.mh-el-drag,
.mh-drag-handle{
  grid-template-columns:repeat(2, 3px); grid-template-rows:repeat(2, 3px);
  gap:2px; padding:4px 5px;
}
.mh-el-num,
.mh-scene-num-badge{ line-height:1; }
.mh-el-num,
.mh-el-drag,
.mh-scene-num-badge,
.mh-drag-handle{ color:color-mix(in srgb, var(--sheet-ink) 34%, transparent); }
.mh-el-drag:hover,
.mh-drag-handle:hover{ color:var(--sheet-ink-soft); background:var(--surface-2); }
.mh-scene-num-badge{
  top:50%; transform:translateY(-50%); left:-42px; width:18px; min-width:0;
}
/* The scene handle is positioned against the WHOLE .mh-scene-block (it is a
   sibling of the headrow, not a child), so top:50% floated it to the vertical
   middle of the entire scene — overlapping element gutters below (the mangled
   dots the user framed). Anchor it to the HEADING line instead: the heading
   button's first line centre sits ~12px from the block top (4px padding + half
   a ~16px line box); the 16px-tall handle needs top ≈ 4px. */
.mh-drag-handle{ top:4px; transform:none; left:-20px; }

/* ── Pass-3 fixes (user-framed prod issues) ──
   1) An EMPTY block always whispers its element type — faint when idle,
      slightly firmer when focused — so a hover gutter never floats over a
      blank void (the orphan "4 ⠿ over nothing" the user framed). */
.mh-el-editable:empty::before,
.mh-el-editable:has(br.ProseMirror-trailingBreak)::before{ font-style:normal; opacity:0.22; }
.mh-el-editable:empty:focus::before,
.mh-el-editable:has(br.ProseMirror-trailingBreak):focus::before{ opacity:0.4; }
.mh-el-editable[data-el-type='action']:empty::before,
.mh-el-editable[data-el-type='action']:has(br.ProseMirror-trailingBreak)::before{ content:'Action'; }
.mh-el-editable[data-el-type='character']:empty::before,
.mh-el-editable[data-el-type='character']:has(br.ProseMirror-trailingBreak)::before{ content:'Character'; }
.mh-el-editable[data-el-type='dialogue']:empty::before,
.mh-el-editable[data-el-type='dialogue']:has(br.ProseMirror-trailingBreak)::before{ content:'Dialogue'; }
.mh-el-editable[data-el-type='paren']:empty::before,
.mh-el-editable[data-el-type='paren']:has(br.ProseMirror-trailingBreak)::before{ content:'Parenthetical'; }
.mh-el-editable[data-el-type='transition']:empty::before,
.mh-el-editable[data-el-type='transition']:has(br.ProseMirror-trailingBreak)::before{ content:'TRANS'; }
.mh-el-editable[data-el-type='comment']:empty::before,
.mh-el-editable[data-el-type='comment']:has(br.ProseMirror-trailingBreak)::before{ content:'Comment'; }
.mh-el-editable[data-el-type='subtitle']:empty::before,
.mh-el-editable[data-el-type='subtitle']:has(br.ProseMirror-trailingBreak)::before{ content:'Subtitle'; }
/* The hollywood cue is CENTERED (text-align:center above) but the placeholder
   overlay is pinned left:0 — an empty cue showed its caret mid-line with the
   "Character" whisper orphaned at the page's left edge (read as "no hint at
   all"). Stretch the overlay across the row and centre its text so the hint
   sits under the caret, laper-style. Asian cues stay text-align:left, so the
   base left:0 overlay already lines up there. */
.mh-el-editable.hw-character:empty::before,
.mh-el-editable.hw-character:has(br.ProseMirror-trailingBreak)::before{
  left:0; right:0; text-align:center;
}
/* 2) Hover tints were the COOL chrome surface (--surface-2, lavender) sitting
      on the WARM paper — read as a wrong-coloured box. Warm ink-mix tints
      instead (the heading tokens carry their own hover — see .mh-scene-select). */
.mh-el-drag:hover,
.mh-drag-handle:hover{ background:color-mix(in srgb, var(--sheet-ink) 7%, transparent); }

/* ── Slash menu (the "/" block-type picker) ── mirrors the mention popup.
   NOTE: never put backticks inside this template literal — one terminated the
   string early and the whole stylesheet evaluated to NaN (total unstyle). */
/* Same paper-toned dropdown DNA as .mh-mention-pop above: one look for every
   floating option list on the sheet — elevated surface, warm-grey selection,
   no brand-indigo fill. */
.mh-slash-menu{
  position:absolute; z-index:30; min-width:190px;
  background:var(--surface); border:1px solid var(--sheet-border);
  border-radius:10px; box-shadow:0 10px 28px rgba(35,20,90,0.14); padding:4px;
  font-family:var(--sans);
}
.mh-slash-item{
  display:flex; align-items:center; gap:9px; width:100%; text-align:left;
  padding:8px 12px; border:none; border-radius:6px; background:none;
  font-size:13px; color:var(--sheet-ink); cursor:pointer;
  transition:background 0.12s ease;
}
.mh-slash-item:hover{ background:color-mix(in srgb, var(--sheet-ink) 6%, transparent); }
.mh-slash-item.active{ background:color-mix(in srgb, var(--sheet-ink) 11%, transparent); color:var(--sheet-ink); }
.mh-slash-glyph{
  width:18px; text-align:center; font-family:var(--mono); font-size:11px;
  color:var(--sheet-ink-soft); flex-shrink:0;
}
.mh-slash-item.active .mh-slash-glyph{ color:var(--sheet-ink); }
.mh-slash-empty{ padding:8px 12px; font-size:12.5px; color:var(--sheet-ink-soft); }

/* ── Paged mode: laper-style page seams. An IN-FLOW band between rows: filler
   pads the current page to fixed height, then a single faint dashed rule runs
   the full paper width with the page number nested in its middle (dash-dash
   NUMBER dash-dash). The paper stays one continuous sheet — no bottom-edge /
   gap / top-edge three-piece break. Rule row height (40px) = SEAM_CHROME_PX
   (paginate.ts). Bleeds past the sheet text padding so the rule runs
   edge-to-edge. */
/* The seam lives in the sheet's CONTENT box, so to bleed edge-to-edge it must
   pull back past the paper's horizontal padding + border on each side. Derive
   both from the shared --sheet-pad-* vars (they resolve to the SAME px here as
   on .mh-sheet — the seam inherits the sheet's font, so its ch matches) rather
   than the old hardcoded -73/-49 that were tied to the retired 72/48px padding.
     left  = padding-left  + border  →  reaches the left  paper edge
     right = padding-right + border  →  reaches the right paper edge */
.mh-page-seam{
  margin:0
    calc(-1 * (var(--sheet-pad-r) + var(--sheet-border-w))) 0
    calc(-1 * (var(--sheet-pad-l) + var(--sheet-border-w)));
  pointer-events:none; user-select:none;
  position:relative; z-index:3;
}
/* IN-EDITOR seam (pageSeamPlugin widget): it renders inside .mh-scene-block,
   which adds padding-left:4px, so the shared left margin lands 4px short of the
   paper's left edge (the scene-level <PageSeam> has no such inset and bleeds
   correctly). Add that 4px back so both seam contexts reach the same edge. The
   right edge already coincides (block has no right padding), so the right
   margin above is unchanged. */
.mh-page-seam-inline{
  margin-left:calc(-1 * (var(--sheet-pad-l) + var(--sheet-border-w) + 4px));
}
/* The dashed rule: a 40px flex row whose two ::before/::after segments grow to
   fill the width, with the page number pinned in the centre. The 1px dashed
   border is drawn on the pseudo-elements in faint sheet ink, so it adapts to
   both light and dark themes via --sheet-ink. */
.mh-page-seam-rule{
  height:40px; display:flex; align-items:center; gap:12px;
}
.mh-page-seam-rule::before,
.mh-page-seam-rule::after{
  content:""; flex:1 1 auto;
  border-top:1px dashed color-mix(in srgb, var(--sheet-ink) 25%, transparent);
}
.mh-page-seam-num{
  flex:0 0 auto;
  font-family:var(--script-mono); font-size:10.5px; line-height:1;
  color:color-mix(in srgb, var(--sheet-ink) 42%, transparent);
}

/* ── Gutter micro-alignment (user pass-4): the block number and the dot
   handle each derived their height from font metrics, reading as vertically
   offset from each other. Pin both to one 16px flex-centred box. */
.mh-el-num,
.mh-scene-num-badge{
  height:16px; display:inline-flex; align-items:center; justify-content:flex-end;
}
.mh-el-drag,
.mh-drag-handle{ height:16px; align-content:center; }

/* The format toolbar pins to the top while the script scrolls (user request:
   menu bar fixed on scroll-down). It is a direct child of the .mh-sheet-scroll
   scrollport, so sticky just works; z sits above the sheet + page-break
   overlays + hover gutters. */
.mh-sheet-scroll > .mh-h-toolbar{ position:sticky; top:0; z-index:40; }

/* ==================================================================
   IN-FLOW GUTTERS — the structural fix for the misalignment CLASS.
   Absolute-positioned gutters needed a hand-tuned top offset per row
   kind and font (Latin vs CJK line boxes), and drifted every time. An
   in-flow flex member SHARES the text's line box, so it can never
   drift vertically: it sits in the left margin via negative
   margin-left (numbers/handles never shift the text grid), align-self
   pins to the FIRST line, align-items centres within that 1.7em box.
   Overrides every earlier absolute/top rule for these elements.
   ================================================================== */
.mh-el-gutter{
  position:static; left:auto; top:auto;
  width:42px; margin-left:-51px; /* 42px box + the row's 9px flex gap */
  flex-shrink:0; align-self:flex-start; height:1.7em;
  display:flex; align-items:center; justify-content:flex-end; gap:4px;
  /* The 1.7em box must equal the TEXT line box (13.5px × 1.7). Legacy
     inherited 13.5px from the row it sat inside; the TipTap NodeView makes
     the gutter a SIBLING of the content div, where it inherits the browser
     default 16px instead — 1.7em became 27.2px and every number sat ~3px
     below its first text line. Pin the font-size so both engines share the
     text's line box. */
  font-size:13.5px;
}
.mh-scene-gutter{
  width:42px; margin-left:-50px; /* 42px box + the headrow's 8px flex gap */
  flex-shrink:0; display:inline-flex; align-items:center;
  justify-content:flex-end; gap:4px;
  opacity:0; transition:opacity 0.12s ease;
}
.mh-scene-block:hover .mh-scene-gutter{ opacity:1; }
.mh-scene-block:focus-within:not(:hover) .mh-scene-gutter{ opacity:0; }
/* Children are always opaque — the WRAPPER gates the reveal now. Neutralise
   the earlier per-child opacity rules at equal-or-higher specificity. */
.mh-scene-num-badge,
.mh-drag-handle,
.mh-scene-block:hover .mh-scene-num-badge,
.mh-scene-block:focus-within .mh-scene-num-badge,
.mh-scene-block:focus-within:not(:hover) .mh-scene-num-badge,
.mh-scene-block:hover .mh-drag-handle,
.mh-scene-block:focus-within .mh-drag-handle{ opacity:1; }
.mh-scene-num-badge{ position:static; left:auto; top:auto; transform:none; width:auto; min-width:0; }
.mh-drag-handle{ position:static; left:auto; top:auto; transform:none; }

/* ── DIFF RAIL — version COMPARE floats in the margin right of the sheet ──
   A zero-height sticky wrapper keeps the card in view while the paper
   scrolls, so the live text and the diff are visible SIDE BY SIDE (the
   version LIST stays in the Writing island). The inner card sits just right
   of the 780px sheet (780/2 = 390 + 16px gap), clamped so a narrow container
   pins it to its own right edge instead of overflowing. Hidden entirely on
   viewports too narrow to have a margin at all. */
.mh-version-rail{
  position:sticky; top:56px; z-index:6;
  width:100%; height:0; overflow:visible;
  pointer-events:none; align-self:stretch;
}
.mh-version-rail-inner{
  position:absolute; top:0;
  left:min(calc(50% + 406px), calc(100% - 356px));
  width:340px; pointer-events:auto;
  background:rgba(255,255,255,0.72); backdrop-filter:blur(8px);
  border:1px solid var(--surface-border); border-radius:10px;
  box-shadow:0 6px 18px rgba(35,20,90,0.08);
  overflow:hidden; display:flex;
}
/* The diff view fills the card and scrolls INSIDE it (its own topbar stays
   pinned) — compacted from its old centre-pane sizing. */
.mh-diff-rail .mh-version-diff{ height:auto; max-height:calc(100vh - 240px); flex:1; min-width:0; }
.mh-diff-rail .mh-diff-topbar{ padding:8px 12px; gap:8px; }
.mh-diff-rail .mh-diff-scroll{ padding:12px; gap:12px; }
.mh-diff-rail .mh-diff-text{ font-size:12px; }
@media (max-width:1180px){ .mh-version-rail{ display:none; } }

/* ==================================================================
   SCENE / ELEMENT DRAG HANDLES — bigger hit target + steadier reveal
   (right-click menu is the other, more reliable entry point).
   ================================================================== */
/* The 4-dot grips were ~18×16 and hard to grab; enlarge the clickable box to
   ~24×24 while keeping the 2×2 dots centred. Later rule → wins the cascade. */
.mh-drag-handle,
.mh-el-drag{
  min-width:22px; min-height:22px; padding:5px 6px;
  align-content:center; justify-content:center;
}
/* Keep the scene gutter up while its block is hovered / focused / being dragged /
   move-armed — so the handle never vanishes the instant the pointer drifts to it. */
.mh-scene-block:hover .mh-scene-gutter,
.mh-scene-block:focus-within .mh-scene-gutter,
.mh-scene-block.dragging .mh-scene-gutter,
.mh-scene-block.move-armed .mh-scene-gutter{ opacity:1; }
/* Element grip stays visible during a drag (the source row shouldn't flicker as
   all rows arm as drop targets). :has matches the row wrapping the dragged grip. */
.mh-el-row:has(.mh-el-drag.dragging) .mh-el-gutter{ opacity:1; pointer-events:auto; }

/* ==================================================================
   SCENE CONTEXT MENU (right-click) + whole-scene MOVE overlay
   ================================================================== */
/* Light popup DNA (matches .mh-slash-menu / .mh-heading-pop); fixed to the cursor
   point (SceneContextMenu clamps it inside the viewport). */
.mh-scene-ctx-menu{
  position:fixed; z-index:50; min-width:172px;
  background:#fff; border:1px solid var(--sheet-border);
  border-radius:8px; box-shadow:0 12px 32px rgba(35,20,90,0.18);
  padding:4px; font-family:var(--sans);
}
.mh-scene-ctx-item{
  display:block; width:100%; text-align:left;
  padding:7px 12px; border:none; background:none; border-radius:5px;
  font-family:var(--sans); font-size:13px; color:var(--sheet-ink);
  cursor:pointer; white-space:nowrap;
}
.mh-scene-ctx-item:hover{ background:var(--surface-2); }
.mh-scene-ctx-item.danger{ color:#dc2626; }
.mh-scene-ctx-item.danger:hover{ background:color-mix(in srgb, #dc2626 10%, transparent); }
.mh-scene-ctx-divider{ height:1px; margin:4px 6px; background:var(--sheet-border); }

/* Whole-scene move mode: a dashed overlay over the block; dragging it anywhere
   moves the entire scene. Sits above the content but below any open menu. */
.mh-scene-move-overlay{
  position:absolute; inset:0; z-index:30;
  display:flex; align-items:flex-start; justify-content:center; padding-top:6px;
  border:2px dashed var(--indigo); border-radius:10px;
  background:color-mix(in srgb, var(--indigo) 8%, transparent);
  cursor:grab;
}
.mh-scene-move-overlay:active{ cursor:grabbing; }
.mh-scene-move-hint{
  font-family:var(--sans); font-size:12px; font-weight:600; color:#fff;
  background:var(--indigo); padding:3px 10px; border-radius:999px;
  box-shadow:0 4px 12px rgba(35,20,90,0.25); pointer-events:none;
}
`;
