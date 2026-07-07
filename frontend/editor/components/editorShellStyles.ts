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
  --sans:-apple-system,"Inter","Segoe UI",Helvetica,Arial,sans-serif;
  position:absolute; inset:0;
  background:radial-gradient(circle at 15% 0%, var(--bg-2) 0%, var(--bg) 55%);
  color:var(--ink);
  font-family:var(--sans);
  display:grid;
  grid-template-columns:auto 1fr auto;
  gap:16px;
  padding:16px;
  overflow:hidden;
}
.mh-editor-shell *{ box-sizing:border-box; }

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
  background:var(--indigo-soft); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); padding:10px 12px;
}
.mh-ep-selector:hover{ border-color:var(--indigo); }
.mh-ep-selector:focus-visible{ outline:2px solid var(--indigo); outline-offset:2px; }
.mh-ep-name{ font-weight:700; font-size:14px; color:var(--indigo-deep); }
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
  background:var(--indigo); color:var(--accent-on); border:1px solid transparent;
}
.mh-ep-new-btn:hover:not(:disabled){ background:var(--indigo-deep); }
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
.mh-ep-item-main.current{ background:var(--indigo-soft); color:var(--indigo-deep); cursor:default; }
.mh-ep-item-title{
  flex:1; min-width:0; font-size:12.5px; font-weight:600;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-ep-item-count{
  flex-shrink:0; font-family:var(--mono); font-size:10.5px; color:var(--ink-faint);
}
.mh-ep-current-badge{
  flex-shrink:0; font-size:9.5px; font-weight:700; letter-spacing:0.04em; text-transform:uppercase;
  padding:1px 6px; border-radius:8px; background:var(--indigo); color:var(--accent-on);
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
  background:var(--indigo-soft); color:var(--indigo-deep);
}
.mh-scene-list{ flex:1; overflow-y:auto; padding:4px 10px 14px; display:flex; flex-direction:column; gap:5px; }
.mh-scene-row{
  display:flex; align-items:flex-start; gap:9px; padding:9px;
  border-radius:var(--radius-sm); cursor:pointer;
  border:1px solid transparent; text-align:left; background:none; width:100%;
  font-family:var(--sans);
}
.mh-scene-row:hover{ background:var(--surface-2); }
.mh-scene-row.active{ background:var(--indigo-soft); border-color:var(--surface-border); }
.mh-scene-num-chip{
  width:21px; height:21px; border-radius:6px; background:var(--ink-faint);
  color:var(--surface); font-size:10.5px; font-weight:700; font-family:var(--mono);
  display:flex; align-items:center; justify-content:center; flex-shrink:0; margin-top:1px;
}
.mh-scene-row.active .mh-scene-num-chip{ background:var(--indigo); color:var(--accent-on); }
.mh-scene-meta-text{ min-width:0; flex:1; }
.mh-scene-row-head{ display:flex; align-items:center; gap:6px; }
.mh-ie-badge{
  font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:0.03em;
  padding:1px 5px; border-radius:4px; flex-shrink:0;
}
.mh-ie-badge.int{ background:var(--indigo-soft); color:var(--indigo-deep); }
.mh-ie-badge.ext{ background:var(--violet-soft); color:var(--violet); }
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
.mh-rail-module.active{ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--surface-border); }
.mh-rail-module-glyph{
  width:16px; text-align:center; flex-shrink:0;
  font-family:var(--mono); font-size:12px; color:var(--ink-faint);
}
.mh-rail-module.active .mh-rail-module-glyph{ color:var(--indigo-deep); }
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
.mh-icon-btn:hover{ background:var(--indigo-soft); color:var(--indigo-deep); }

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
.mh-doc-tab[aria-selected='true']{ background:var(--indigo); color:var(--accent-on); }
.mh-topbar-right{ display:flex; align-items:center; gap:10px; }

/* ── Collaboration presence (Phase B P5 / C1) ─────────────────────────────── */
.mh-presence-avatars{ display:flex; align-items:center; }
.mh-presence-avatar{
  display:inline-flex; align-items:center; justify-content:center;
  width:26px; height:26px; margin-left:-7px; border-radius:50%;
  background:var(--indigo-soft); color:var(--indigo-deep);
  border:2px solid var(--surface); box-shadow:var(--shadow-island);
  font-size:11px; font-weight:600; font-family:var(--sans);
  user-select:none;
}
.mh-presence-avatar:first-child{ margin-left:0; }
.mh-presence-avatar.editing{ background:var(--indigo); color:var(--accent-on); }
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
  background:var(--indigo-soft); color:var(--indigo-deep); border-color:transparent;
}
.mh-flow-scene-head .mh-scene-presence-badge{ margin-left:auto; }

.mh-page-frame{ flex:1; min-height:0; display:flex; }
.mh-sheet-scroll{
  flex:1; min-height:0; min-width:0; overflow-y:auto;
  display:flex; flex-direction:column; align-items:center; gap:16px;
  background:var(--surface-2); border:1px solid var(--surface-border);
  border-radius:var(--radius-lg); padding:20px 26px 22px;
}
.mh-sheet{
  width:820px; max-width:100%;
  background:linear-gradient(180deg, var(--sheet-bg) 0%, var(--sheet-bg-2) 100%);
  border:1px solid var(--sheet-border); border-radius:6px;
  box-shadow:var(--shadow-sheet); padding:34px 40px 44px;
  font-family:var(--mono); color:var(--sheet-ink);
  position:relative; min-height:60%; isolation:isolate;
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
.mh-drag-handle{
  position:absolute; left:-16px; top:0; color:var(--ink-faint);
  font-size:13px; font-weight:700; letter-spacing:1px; cursor:grab;
  user-select:none; opacity:0; font-family:var(--sans); background:none; border:none;
}
.mh-scene-block:hover .mh-drag-handle,
.mh-scene-block:focus-within .mh-drag-handle{ opacity:1; }
.mh-scene-headrow{ display:flex; align-items:center; gap:8px; margin-bottom:13px; flex-wrap:wrap; }
.mh-scene-select{
  font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:0.03em;
  padding:4px 8px; border-radius:6px; border:1px solid var(--sheet-border);
  background:var(--surface-2); color:var(--sheet-ink-soft); cursor:pointer;
}
.mh-scene-loc-input{
  font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:0.03em;
  padding:4px 9px; border-radius:6px; border:1px solid var(--sheet-border);
  background:var(--surface-2); color:var(--sheet-ink); min-width:130px;
}
/* Read-mode scene heading (Task 4.5): a typographic slug that reads like a
   script heading, not a form. Clicking it reveals the selects above. */
.mh-scene-heading-display{
  flex:1; min-width:0; text-align:left; background:none; border:1px solid transparent;
  border-radius:6px; padding:4px 8px; cursor:text;
  font-family:var(--mono); font-size:13.5px; font-weight:700; letter-spacing:0.04em;
  text-transform:uppercase; color:var(--sheet-ink);
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
}
.mh-scene-heading-display.asian{
  font-family:var(--sans); font-size:14px; letter-spacing:0.02em; text-transform:none;
}
.mh-scene-heading-display:hover{ background:var(--surface-2); }
.mh-scene-heading-display:focus-visible{ outline:2px solid var(--indigo); outline-offset:-2px; }
.mh-scene-heading-empty{ color:var(--sheet-ink-soft); font-weight:600; text-transform:none; font-style:italic; }

/* ===== ELEMENT ROWS (Hollywood layout engine) ===== */
.mh-el-row{ display:flex; align-items:flex-start; gap:9px; margin:0 0 7px; }
.mh-el-tick{ width:3px; border-radius:2px; flex-shrink:0; align-self:stretch; min-height:18px; margin-top:3px; }
.mh-el-tick.t-action{ background:var(--tick-action); }
.mh-el-tick.t-dialogue{ background:var(--tick-dialogue); box-shadow:var(--tick-glow); }
.mh-el-tick.t-character{ background:var(--tick-character); box-shadow:var(--tick-glow); }
.mh-el-tick.t-paren{ background:var(--tick-paren); }
.mh-el-tick.t-transition{ background:var(--tick-transition); }
.mh-el-tick.t-comment{ background:var(--tick-comment); }
.mh-el-tick.t-subtitle{ background:var(--tick-subtitle); }
.mh-el-editable{
  font-size:13.5px; line-height:1.7; flex:1; outline:none;
  font-family:var(--mono); color:var(--sheet-ink);
  white-space:pre-wrap; word-break:break-word; min-height:1.7em;
}
.mh-el-editable:empty::before{
  content:attr(data-placeholder); color:var(--sheet-ink-soft); font-style:italic;
}
.mh-el-row.focused .mh-el-editable{
  box-shadow:0 0 0 2px color-mix(in srgb, var(--indigo) 32%, transparent);
  border-radius:4px;
}
/* Hollywood metrics — spec D8 column 1 */
.hw-action{ text-align:left; }
.hw-character{ margin-left:38%; text-transform:uppercase; font-weight:700; letter-spacing:0.03em; color:var(--tick-character); }
.hw-dialogue{ margin:0 22%; }
.hw-paren{ margin:0 30%; font-style:italic; color:var(--sheet-ink-soft); }
.hw-transition{ text-align:right; text-transform:uppercase; font-weight:700; letter-spacing:0.04em; color:var(--sheet-ink-soft); }
.hw-comment{ border-left:3px solid var(--tick-comment); padding-left:8px; color:var(--sheet-ink-soft); font-style:italic; }
.hw-subtitle{ text-align:center; font-style:italic; color:var(--sheet-ink-soft); }
.mh-el-row.transition-row{ justify-content:flex-end; }
.mh-el-row.transition-row .mh-el-tick{ display:none; }

/* ===== ELEMENT ROWS (Asian layout engine) — spec D8 column 2 ===== */
/* A numbered-manuscript typeset: △ action prefix, Name: character label,
   indented dialogue, inline parens, right transitions, quoted comments,
   centred subtitles. Colours come from the theme-scoped variables above, so
   light and dark are both covered without extra selectors. */
.as-row{ display:flex; align-items:baseline; gap:6px; margin:0 0 7px; }
.as-row .mh-el-tick{ display:none; }
.as-row .mh-el-row{ flex:1 1 auto; margin:0; }
.as-mark{
  font-family:var(--mono); font-size:13.5px; line-height:1.7;
  color:var(--sheet-ink-soft); flex-shrink:0; user-select:none;
}
.as-prefix{ color:var(--tick-transition); font-weight:700; }
.as-suffix{ margin-left:-3px; color:var(--tick-character); font-weight:700; }
/* Character cue = left-aligned label (NOT centred like Hollywood); the row
   packs cue + colon to the left instead of letting the editable stretch. */
.as-row-character{ justify-content:flex-start; }
.as-row-character .mh-el-row{ flex:0 1 auto; }
.as-row-character .mh-el-editable{ flex:0 1 auto; }
.as-row-dialogue{ padding-left:2.4em; }
.as-row-transition{ justify-content:flex-end; }
.as-row-subtitle{ justify-content:center; }
.as-action{ text-align:left; }
.as-character{ font-weight:700; letter-spacing:0.03em; color:var(--tick-character); text-align:left; }
.as-dialogue{ text-align:left; }
.as-paren{ font-style:italic; color:var(--sheet-ink-soft); }
.as-transition{ text-transform:uppercase; font-weight:700; letter-spacing:0.04em; color:var(--sheet-ink-soft); }
.as-comment{ border-left:3px solid var(--tick-comment); padding-left:8px; color:var(--sheet-ink-soft); font-style:italic; }
.as-subtitle{ text-align:center; font-style:italic; color:var(--sheet-ink-soft); }

/* Asian scene head = a flat numbered line (N. …) rather than a chip badge. */
.mh-scene-headrow.asian .mh-scene-num-badge{
  width:auto; height:auto; min-width:0; padding:0 4px 0 0;
  background:transparent; color:var(--sheet-ink);
  font-size:14px; font-weight:700;
}
.mh-editor-shell[data-theme='dark'] .mh-scene-headrow.asian .mh-scene-num-badge{
  background:transparent; color:var(--sheet-ink);
}

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
  border-color:var(--indigo);
  box-shadow:var(--shadow-float), 0 0 0 1px var(--indigo-soft);
}
.mh-h-item{
  display:flex; align-items:center; gap:6px; padding:7px 13px; border-radius:999px;
  font-size:12px; font-weight:600; color:var(--ink-soft); white-space:nowrap;
  cursor:pointer; background:none; border:none; font-family:var(--sans);
}
.mh-h-item:hover:not(:disabled){ background:var(--surface-2); }
.mh-h-item:disabled{ opacity:0.5; cursor:default; }
.mh-h-item.active{ background:var(--indigo); color:var(--accent-on); }
.mh-h-glyph{
  width:16px; height:16px; display:inline-flex; align-items:center; justify-content:center;
  font-size:9.5px; font-family:var(--mono); font-weight:700; color:var(--ink-faint); flex-shrink:0;
}
.mh-h-item.active .mh-h-glyph{ color:var(--accent-on); }

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
.mh-seg.active{ background:var(--indigo); color:var(--accent-on); }
.mh-seg:disabled{ opacity:0.45; cursor:default; }
.mh-divider{ height:1px; background:var(--surface-border); margin:0 -16px; }
.mh-stats-grid{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.mh-stat-tile{
  background:var(--indigo-soft); border:1px solid var(--surface-border);
  border-radius:12px; padding:12px 12px 10px;
}
.mh-stat-tile.v2{ background:var(--violet-soft); }
.mh-stat-num{ font-size:19px; font-weight:800; color:var(--indigo-deep); font-family:var(--mono); }
.mh-stat-tile.v2 .mh-stat-num{ color:var(--violet); }
.mh-stat-lbl{ font-size:10.5px; color:var(--ink-faint); font-weight:600; margin-top:2px; }
.mh-cast-row{ display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink-soft); padding:3px 0; }
.mh-cast-dot{ width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.mh-scene-num-badge{
  width:24px; height:24px; border-radius:7px; background:var(--ink);
  color:var(--surface); font-size:11px; font-weight:700; font-family:var(--mono);
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.mh-editor-shell[data-theme='dark'] .mh-scene-num-badge{ background:var(--indigo); color:var(--accent-on); }
.mh-scene-chip{ font-size:11px; font-weight:700; letter-spacing:0.03em; padding:4px 9px; border-radius:6px; font-family:var(--mono); }
.mh-scene-chip.ie-int{ background:var(--indigo-soft); color:var(--indigo-deep); }
.mh-scene-chip.ie-ext{ background:var(--violet-soft); color:var(--violet); }
.mh-scene-chip.loc{ background:var(--surface-2); color:var(--sheet-ink-soft); border:1px solid var(--sheet-border); }
.mh-el-line{ font-size:13.5px; line-height:1.7; color:var(--sheet-ink); margin-bottom:7px; }
.mh-placeholder-line{ color:var(--sheet-ink-soft); font-style:italic; }

/* ===== @ MENTION CHIPS + PICKER ===== */
.mh-mention{
  color:var(--indigo-deep); background:var(--indigo-soft);
  border-radius:5px; padding:0 4px; font-weight:600;
  box-decoration-break:clone; -webkit-box-decoration-break:clone;
}
.mh-mention.unknown{ color:var(--ink-faint); background:var(--surface-2); font-weight:500; }
.mh-mention-pop{
  z-index:20; min-width:180px; max-width:260px; margin-top:2px;
  background:var(--surface); border:1px solid var(--surface-border);
  border-radius:var(--radius-md); box-shadow:var(--shadow-float);
  padding:5px; font-family:var(--sans);
}
.mh-mention-list{ list-style:none; margin:0; padding:0; max-height:220px; overflow-y:auto; }
.mh-mention-opt{
  padding:7px 10px; border-radius:var(--radius-sm); font-size:12.5px;
  color:var(--ink-soft); cursor:pointer; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis;
}
.mh-mention-opt:hover,
.mh-mention-opt.active{ background:var(--indigo-soft); color:var(--indigo-deep); }
.mh-mention-opt[aria-selected='true']{ font-weight:600; }
.mh-mention-empty{ padding:8px 10px; font-size:12px; color:var(--ink-faint); }

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
  flex-direction:column; gap:10px; background:var(--bg); color:var(--ink-soft);
  font-family:var(--sans); font-size:14px; padding:24px; text-align:center;
}
.mh-shell-state.error{ color:var(--red); }

/* ===== SCENE REORDER (drag + keyboard, Task 10) ===== */
.mh-drag-handle{ cursor:grab; border-radius:5px; }
.mh-drag-handle:active{ cursor:grabbing; }
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

/* ===== VISIBLE FOCUS RING AUDIT (a11y sweep, Task 10) ===== */
.mh-editor-shell button:focus-visible,
.mh-editor-shell select:focus-visible,
.mh-editor-shell input:focus-visible,
.mh-editor-shell [role='tab']:focus-visible,
.mh-editor-shell [role='option']:focus-visible,
.mh-editor-shell .mh-el-editable:focus-visible{
  outline:2px solid var(--indigo);
  outline-offset:2px;
  border-radius:5px;
}
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
  background:var(--surface-2); border:1px solid var(--surface-border);
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
.mh-flow-handle{ width:7px; height:7px; background:var(--indigo); border:none; }
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
  background:var(--indigo-soft); color:var(--indigo-deep);
}
.mh-flow-chapter{ width:200px; background:var(--indigo-soft); border-color:var(--surface-border); }
.mh-flow-chapter-head{ display:flex; align-items:center; gap:8px; }
.mh-flow-chapter-title{ font-size:13px; font-weight:700; color:var(--indigo-deep); }
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
  background:var(--indigo); color:var(--accent-on); border-color:var(--indigo);
}

/* ===== STORYBOARD VIEW (scene columns + shot cards) ===== */
.mh-storyboard{
  flex:1; min-height:0; min-width:0;
  background:var(--surface-2); border:1px solid var(--surface-border);
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
  background:var(--indigo); color:var(--accent-on); border:1px solid transparent;
}
.mh-sb-auto-btn:hover:not(:disabled){ background:var(--indigo-deep); }
.mh-sb-auto-btn:disabled{ opacity:0.6; cursor:default; }
.mh-sb-auto-btn.confirming{ background:var(--violet); border-color:var(--violet); }
.mh-sb-shots{ flex:1; min-height:0; overflow-y:auto; display:flex; flex-direction:column; gap:8px; padding:2px; }
.mh-sb-empty{ padding:14px 8px; font-size:12px; color:var(--ink-faint); font-style:italic; text-align:center; }
.mh-sb-empty-board{ margin:auto; font-size:13px; color:var(--ink-faint); font-style:italic; }
.mh-sb-add{
  flex-shrink:0; font-family:var(--sans); font-size:12px; font-weight:600;
  padding:8px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface-2); color:var(--ink-soft);
  border:1px dashed var(--surface-border);
}
.mh-sb-add:hover{ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--indigo); }

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
.mh-shot-num{ flex-shrink:0; }
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
  background:var(--indigo-soft); color:var(--indigo-deep); border:1px solid transparent;
}
.mh-shot-pill:hover{ border-color:var(--indigo); }
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
.mh-shot-generate:hover:not(:disabled){ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--indigo); }
.mh-shot-generate:disabled{ opacity:0.5; cursor:default; }
.mh-shot-generate-video{
  flex:1; font-family:var(--sans); font-size:11.5px; font-weight:600;
  padding:6px 10px; border-radius:var(--radius-sm); cursor:pointer;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-shot-generate-video:hover:not(:disabled){ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--indigo); }
.mh-shot-generate-video:disabled{ opacity:0.5; cursor:default; }
.mh-shot-generate-video.confirming{ background:var(--indigo); color:#fff; border-color:var(--indigo); }
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
.mh-version-item{
  display:flex; flex-direction:column; gap:6px; padding:8px 10px;
  border:1px solid var(--surface-border); border-radius:var(--radius-sm);
  background:var(--surface-2);
}
.mh-version-item-main{ display:flex; flex-direction:column; gap:2px; }
.mh-version-msg{ font-size:12.5px; font-weight:600; color:var(--ink); word-break:break-word; }
.mh-version-time{ font-family:var(--mono); font-size:10.5px; color:var(--ink-faint); }
.mh-version-actions{ display:flex; flex-wrap:wrap; gap:5px; }
.mh-version-action{
  font-family:var(--sans); font-size:11px; font-weight:600; cursor:pointer;
  padding:3px 9px; border-radius:999px;
  background:var(--surface); color:var(--ink-soft); border:1px solid var(--surface-border);
}
.mh-version-action:hover:not(:disabled){ background:var(--indigo-soft); color:var(--indigo-deep); border-color:var(--indigo); }
.mh-version-action:disabled{ opacity:0.5; cursor:default; }
.mh-version-action.confirming{ background:var(--indigo); color:var(--accent-on); border-color:transparent; }
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
.mh-diff-scene-set{ display:flex; flex-direction:column; gap:6px; }
.mh-diff-scene-chip{ display:flex; align-items:center; gap:8px; font-size:12.5px; color:var(--ink); }
.mh-diff-scene-name{ font-weight:600; }
.mh-diff-scene{ display:flex; flex-direction:column; gap:8px; }
.mh-diff-scene-heading{
  margin:0; font-family:var(--mono); font-size:11px; font-weight:700; letter-spacing:0.04em;
  text-transform:uppercase; color:var(--ink-faint);
}
.mh-diff-changes{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:8px; }
.mh-diff-change{ display:flex; gap:10px; align-items:flex-start; }
.mh-diff-badge{
  flex-shrink:0; font-family:var(--mono); font-size:9.5px; font-weight:700; letter-spacing:0.03em;
  text-transform:uppercase; padding:3px 7px; border-radius:999px; margin-top:1px;
}
.mh-diff-badge.added{ background:var(--green-soft); color:var(--green); }
.mh-diff-badge.removed{ background:var(--red-soft,var(--surface-2)); color:var(--red); }
.mh-diff-badge.changed{ background:var(--amber-soft); color:var(--amber); }
.mh-diff-badge.moved{ background:var(--indigo-soft); color:var(--indigo-deep); }
.mh-diff-texts{ flex:1; min-width:0; display:flex; flex-direction:column; gap:4px; }
.mh-diff-text{ font-size:13px; line-height:1.5; padding:3px 8px; border-radius:5px; word-break:break-word; }
.mh-diff-text.before{ background:var(--red-soft,var(--surface-2)); color:var(--red); text-decoration:line-through; }
.mh-diff-text.after{ background:var(--green-soft); color:var(--green); }

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
  margin:auto; max-width:420px; text-align:center; display:flex; flex-direction:column;
  align-items:center; gap:10px; padding:56px 24px;
}
.mh-beats-empty-title{ font-size:16px; font-weight:800; color:var(--ink); }
.mh-beats-empty-sub{ font-size:13px; line-height:1.6; color:var(--ink-faint); margin:0; }

.mh-beat-card{
  border:1px solid var(--hairline); border-radius:var(--radius-md);
  background:var(--surface); padding:10px 12px; margin:5px 0;
  display:flex; flex-direction:column; gap:8px;
}
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
.mh-beat-notes-toggle[aria-expanded="true"]{ color:var(--indigo-deep); border-color:var(--indigo); }
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
  display:inline-flex; align-items:center; gap:2px; background:var(--indigo-soft);
  border-radius:999px; padding:0 2px 0 0; max-width:100%;
}
.mh-beat-chip-label{
  font-size:11.5px; font-weight:600; color:var(--indigo-deep); background:transparent;
  border:none; border-radius:999px; padding:3px 4px 3px 10px; cursor:pointer;
  max-width:220px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.mh-beat-chip-label:disabled{ cursor:default; color:var(--ink-faint); }
.mh-beat-chip-x{
  font-size:12px; line-height:1; color:var(--indigo-deep); background:transparent; border:none;
  border-radius:999px; width:16px; height:16px; cursor:pointer; opacity:0.65;
}
.mh-beat-chip-x:hover{ opacity:1; }
.mh-beat-link-select{
  font-family:var(--sans); font-size:11.5px; color:var(--ink-faint); background:var(--surface-2);
  border:1px solid var(--hairline); border-radius:999px; padding:3px 8px; cursor:pointer;
}
`;
