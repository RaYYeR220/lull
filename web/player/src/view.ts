/**
 * The player's shadow DOM: markup, styles and glyphs. Static strings only; every piece of
 * text that comes from a file (cue lines, URLs) is set with textContent by the element.
 *
 * Styles read the site's Nocturne tokens (--night-*, --cream-*, --lamp-*, --f-*) when the
 * page defines them and fall back to the same values, so an embed off-site looks the same
 * apart from the faces it can't load.
 */

/* The shared glyphs: 12-unit grid, square caps, currentColor. Decorative; a word always
   travels with them (visibly or as the control's name). */
const g = (w: number, body: string) =>
  `<svg width="${(14 * w) / 12}" height="14" viewBox="0 0 ${w} 12" aria-hidden="true" focusable="false">${body}</svg>`;

export const ICON = {
  play: g(12, '<path d="M3 1.8v8.4L10 6z" fill="currentColor"/>'),
  pause: g(12, '<path d="M3 1.8h2v8.4H3zM7 1.8h2v8.4H7z" fill="currentColor"/>'),
  replay: g(12, '<path d="M2.4 6a3.6 3.6 0 1 0 1.1-2.6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M2 1.4v3h3" fill="none" stroke="currentColor" stroke-width="1.5"/>'),
  voice: g(14, '<path d="M1 4.2h2.6L7 1.2v9.6L3.6 7.8H1z" fill="currentColor"/><path d="M9.4 3.6a3.4 3.4 0 0 1 0 4.8M11.4 1.8a6 6 0 0 1 0 8.4" fill="none" stroke="currentColor" stroke-width="1.3"/>'),
  muted: g(14, '<path d="M1 4.2h2.6L7 1.2v9.6L3.6 7.8H1z" fill="currentColor"/><path d="M9.2 4.2l3.6 3.6M12.8 4.2 9.2 7.8" fill="none" stroke="currentColor" stroke-width="1.3"/>'),
  extended: g(12, '<path d="M1 1.5v9M11 1.5v9M3.2 6h5.6M5 4.2 3.2 6 5 7.8M7 4.2 8.8 6 7 7.8" fill="none" stroke="currentColor" stroke-width="1.4"/>'),
  ok: g(12, '<path d="M1.5 6.4 4.6 9.4 10.5 2.6" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="square"/>'),
  captions: g(12, '<path d="M.75 2.25h10.5v7.5H.75z" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3 5.4h2.2M6.4 5.4H9M3 7.4h4.4" fill="none" stroke="currentColor" stroke-width="1.2"/>'),
  transcript: g(12, '<path d="M1 2.5h10M1 6h10M1 9.5h6" fill="none" stroke="currentColor" stroke-width="1.4"/>'),
  keys: g(12, '<path d="M.75 2.75h10.5v6.5H.75z" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M2.8 4.9h1.2M5.4 4.9h1.2M8 4.9h1.2M3.4 7.1h5.2" fill="none" stroke="currentColor" stroke-width="1.1"/>'),
  enter: g(12, '<path d="M1 4.5V1h3.5M7.5 1H11v3.5M11 7.5V11H7.5M4.5 11H1V7.5" fill="none" stroke="currentColor" stroke-width="1.4"/>'),
  exit: g(12, '<path d="M4.5 1v3.5H1M11 4.5H7.5V1M7.5 11V7.5H11M1 7.5h3.5V11" fill="none" stroke="currentColor" stroke-width="1.4"/>'),
  caret: g(12, '<path d="M2.5 7.6 6 4.1l3.5 3.5" fill="none" stroke="currentColor" stroke-width="1.4"/>'),
} as const;

export const STYLE = /* css */ `
:host{
  --lp-n0:var(--night-0,#0d0f17);--lp-n1:var(--night-1,#13151f);--lp-n2:var(--night-2,#1a1d2b);
  --lp-n4:var(--night-4,#31364c);
  --lp-c0:var(--cream-0,#f6eedf);--lp-c1:var(--cream-1,#d6cdbb);--lp-c2:var(--cream-2,#aca491);--lp-c3:var(--cream-3,#8d8676);
  --lp-l0:var(--lamp-0,#f6ddb0);--lp-l1:var(--lamp-1,#e9cb98);--lp-l2:var(--lamp-2,#c9a46a);
  --lp-cream:var(--cream-rgb,246 238 223);--lp-lamp:var(--lamp-rgb,233 203 152);
  --lp-hair-faint:rgb(var(--lp-cream) / .06);--lp-hair:rgb(var(--lp-cream) / .10);--lp-hair-strong:rgb(var(--lp-cream) / .18);
  --lp-display:var(--f-display,"Newsreader","Iowan Old Style","Palatino Linotype",Georgia,serif);
  --lp-ui:var(--f-ui,"Figtree",ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif);
  --lp-mono:var(--f-mono,"IBM Plex Mono",ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace);
  --lp-ease:var(--ease-out,cubic-bezier(.16,1,.3,1));--lp-hover:var(--t-hover,.35s);--lp-press:var(--t-press,.12s);
  --lp-hit:44px;--lp-pad:clamp(14px,2.4cqw,24px);--lp-cue:20px;
  display:block;position:relative;container:lull / inline-size;
  color:var(--lp-c1);font-family:var(--lp-ui);font-size:15px;line-height:1.4;font-weight:400;text-align:left;
  font-variant-numeric:lining-nums tabular-nums;-webkit-font-smoothing:antialiased;
}
:host([hidden]){display:none}
*,*::before,*::after{box-sizing:border-box}
button,input{font:inherit;color:inherit;margin:0}
p,ol,dl,dd,dt{margin:0;padding:0}
ol{list-style:none}
[hidden]{display:none!important}
svg{display:block;flex:none}
.sr-only{position:absolute!important;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
:focus:not(:focus-visible){outline:none}
:focus-visible{outline:2px solid var(--lp-l0);outline-offset:3px}

.player{position:relative;display:flex;flex-direction:column;background:var(--lp-n1);border:1px solid var(--lp-hair-strong)}

/* the picture */
.stage{position:relative;background:var(--lp-n0);aspect-ratio:var(--lp-ar,16 / 9);overflow:hidden;cursor:pointer}
.stage:focus-visible{outline-offset:-2px}
video{display:block;width:100%;height:100%;object-fit:contain;background:var(--lp-n0)}
video::cue{font-family:var(--lp-ui);background:rgb(13 15 23 / .82);color:var(--lp-c0)}
.notice{position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;align-items:flex-start;gap:10px;padding:clamp(20px,6cqw,64px);background:var(--lp-n0);cursor:default}
.n-title{font-family:var(--lp-display);font-optical-sizing:auto;font-weight:400;font-size:22px;line-height:1.1;letter-spacing:-.015em;color:var(--lp-c0)}
.n-body{font-size:15.5px;line-height:1.55;color:var(--lp-c1);max-width:34em}
.n-url{font-family:var(--lp-mono);font-size:12px;line-height:1.5;color:var(--lp-c2);overflow-wrap:anywhere;max-width:100%}

/* the voice band: the line Lull speaks, lit from the lamp */
.band{display:flex;flex-direction:column;justify-content:center;gap:8px;min-height:calc(var(--lp-cue) * 2.6 + 30px);padding:15px var(--lp-pad);background:var(--lp-n0);border-top:1px solid var(--lp-hair-faint)}
.hold{display:flex;align-items:center;flex-wrap:wrap;gap:4px 0;font-size:12.5px;font-weight:600;letter-spacing:.01em;line-height:1.2;color:var(--lp-c0)}
.hold > span{display:inline-flex;align-items:center;gap:6px}
.hold > span + span::before{content:"";width:3px;height:3px;margin:0 10px;background:var(--lp-l2)}
.hold .w{font-weight:500;color:var(--lp-c1)}
.line{max-width:46em;padding-left:12px;border-left:1px solid rgb(var(--lp-lamp) / .55);font-family:var(--lp-display);font-optical-sizing:auto;font-style:italic;font-weight:350;font-size:var(--lp-cue);line-height:1.3;letter-spacing:-.005em;color:var(--lp-l0);text-wrap:pretty;transition:color .4s,border-color .4s}
.band[data-state="rest"] .line{color:var(--lp-c2);border-left-color:var(--lp-hair-strong)}
.idle{font-size:13.5px;line-height:1.5;color:var(--lp-c2)}

/* the rail: the programme as a line, each description a lit span on it */
.rail{position:relative;height:var(--lp-hit);margin:2px var(--lp-pad) 0}
.rail:has(.seek:focus-visible){outline:2px solid var(--lp-l0);outline-offset:2px}
.track{position:absolute;left:0;right:0;top:50%;height:2px;margin-top:-1px;background:var(--lp-n4);pointer-events:none}
.fill{position:absolute;inset:0;transform-origin:0 50%;transform:scaleX(var(--lp-p,0));background:var(--lp-c1)}
.spans i{position:absolute;top:-5px;height:12px;min-width:2px;background:rgb(var(--lp-lamp) / .36)}
.spans i.on{background:var(--lp-l1);box-shadow:0 0 14px rgb(var(--lp-lamp) / .5)}
.spans b{position:absolute;top:-9px;width:2px;height:20px;margin-left:-1px;background:rgb(var(--lp-cream) / .3)}
.player[data-mode="extended"] .spans b{background:var(--lp-c0)}
.spans b.on{background:var(--lp-l0);box-shadow:0 0 12px rgb(var(--lp-lamp) / .55)}
.head{position:absolute;top:-10px;left:calc(var(--lp-p,0) * 100%);width:2px;height:22px;margin-left:-1px;background:var(--lp-l0)}
.seek{position:absolute;inset:0;width:100%;height:100%;margin:0;padding:0;opacity:0;cursor:pointer;-webkit-appearance:none;appearance:none;background:transparent}
.seek::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:2px;height:var(--lp-hit)}
.seek::-moz-range-thumb{width:2px;height:var(--lp-hit);border:0}
.seek:disabled{cursor:default}

/* the controls */
.bar{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px 16px;padding:6px var(--lp-pad) 12px}
.left,.right{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-width:var(--lp-hit);height:var(--lp-hit);padding:0 14px;font-size:14px;font-weight:500;letter-spacing:.005em;line-height:1;white-space:nowrap;color:var(--lp-c1);background:transparent;border:1px solid var(--lp-hair-strong);border-radius:0;cursor:pointer;-webkit-tap-highlight-color:transparent;transition:color var(--lp-hover),border-color var(--lp-hover),background-color var(--lp-hover)}
.btn.icon{padding:0}
.btn:hover{color:var(--lp-c0);border-color:rgb(var(--lp-cream) / .45);background:rgb(var(--lp-cream) / .04)}
.btn:active{background:rgb(var(--lp-cream) / .08)}
.btn[aria-pressed="true"],.btn[aria-expanded="true"]{color:var(--lp-l0);border-color:rgb(var(--lp-lamp) / .5)}
.btn:disabled{color:var(--lp-c3);border-color:var(--lp-hair);background:transparent;cursor:not-allowed}

/* play is the lit door: a cream face, a deep-lamp ledge, a lamp glow */
.play{position:relative;display:inline-flex;align-items:center;justify-content:center;width:56px;height:var(--lp-hit);padding:0;border:0;border-radius:0;color:var(--lp-n1);cursor:pointer;-webkit-tap-highlight-color:transparent;
  background:linear-gradient(180deg,#fcf6ea 0%,#f3e8d3 100%);
  box-shadow:0 0 0 1px rgb(var(--lp-lamp) / .7),0 0 26px -6px rgb(var(--lp-lamp) / .42);
  transition:box-shadow .5s var(--lp-ease),transform var(--lp-press) ease-out}
.play::after{content:"";position:absolute;left:0;right:0;bottom:0;height:3px;background:var(--lp-l2);transition:height var(--lp-press) ease-out}
.play:hover{background:linear-gradient(180deg,#fffaf1 0%,#f7eedc 100%);box-shadow:0 0 0 1px rgb(var(--lp-lamp) / .9),0 0 34px -4px rgb(var(--lp-lamp) / .58)}
.play:active{transform:translateY(2px);background:linear-gradient(180deg,#f1e4cb 0%,#eadbbf 100%)}
.play:active::after{height:1px}
.play:focus-visible{outline-offset:5px}
.play:disabled{background:var(--lp-n2);color:var(--lp-c3);box-shadow:0 0 0 1px var(--lp-hair);cursor:not-allowed;transform:none}
.play:disabled::after{display:none}

.time{padding:0 6px;font-family:var(--lp-mono);font-size:12.5px;line-height:1;color:var(--lp-c1);white-space:nowrap}
.time .of{color:var(--lp-c2)}

.modewrap{position:relative}
.mode .k{color:var(--lp-c2)}
.mode .v{color:var(--lp-c0)}
.mode[aria-expanded="true"] .v{color:var(--lp-l0)}
.menu{position:absolute;right:0;bottom:calc(100% + 10px);z-index:5;width:min(400px,calc(100cqw - 2 * var(--lp-pad)));padding:4px 0;background:var(--lp-n2);border:1px solid var(--lp-hair-strong)}
.menu-title{padding:10px 16px 8px;font-size:13.5px;font-weight:500;color:var(--lp-c2)}
.opt{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;row-gap:3px;align-items:baseline;padding:11px 16px 12px 14px;border-top:1px solid var(--lp-hair-faint);border-left:2px solid transparent;cursor:pointer;transition:background-color var(--lp-hover)}
.opt input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:inherit}
.o-name{font-size:15px;font-weight:500;color:var(--lp-c0)}
.o-mark{grid-column:2;grid-row:1;align-self:center;color:var(--lp-l0);visibility:hidden}
.o-desc,.o-why{grid-column:1 / -1;font-size:13.5px;line-height:1.45;color:var(--lp-c2)}
.o-why{color:var(--lp-c1)}
.opt:hover{background:rgb(var(--lp-cream) / .04)}
.opt[data-checked]{border-left-color:var(--lp-l1);background:linear-gradient(90deg,rgb(var(--lp-lamp) / .08),transparent 80%)}
.opt[data-checked] .o-name{color:var(--lp-l0)}
.opt[data-checked] .o-mark{visibility:visible}
.opt:focus-within{outline:2px solid var(--lp-l0);outline-offset:-4px}
.opt:has(input:focus:not(:focus-visible)){outline:none}
.opt[data-off]{cursor:not-allowed}
.opt[data-off]:hover{background:transparent}
.opt[data-off] .o-name{color:var(--lp-c2)}

.speed{padding:0 10px}
.vol{display:flex;align-items:center;gap:6px}
.volume{width:92px;height:var(--lp-hit);margin:0;background:transparent;cursor:pointer;-webkit-appearance:none;appearance:none}
.volume:disabled{opacity:.4;cursor:not-allowed}
.volume::-webkit-slider-runnable-track{height:2px;background:linear-gradient(90deg,var(--lp-c1) 0 var(--lp-v,100%),var(--lp-n4) var(--lp-v,100%) 100%)}
.volume::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:10px;height:18px;margin-top:-8px;background:var(--lp-c0);border:0;border-radius:0}
.volume::-moz-range-track{height:2px;background:var(--lp-n4)}
.volume::-moz-range-progress{height:2px;background:var(--lp-c1)}
.volume::-moz-range-thumb{width:10px;height:18px;background:var(--lp-c0);border:0;border-radius:0}

/* what the voice is, under the controls */
.meta{display:flex;align-items:center;flex-wrap:wrap;gap:4px 0;padding:0 var(--lp-pad) 14px;font-size:13.5px;line-height:1.5;color:var(--lp-c2)}
.meta > span{display:inline-flex;align-items:center;gap:7px}
.meta > span + span::before{content:"";width:3px;height:3px;margin:0 12px;background:var(--lp-l2)}
.meta svg{color:var(--lp-l1)}

/* panels: the transcript and the keyboard map */
.panel{border-top:1px solid var(--lp-hair)}
.p-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:14px var(--lp-pad) 10px;font-size:13.5px;font-weight:500;color:var(--lp-c2)}
.lines{max-height:min(360px,60vh);overflow-y:auto;border-top:1px solid var(--lp-hair-faint);scrollbar-width:thin;scrollbar-color:var(--lp-n4) transparent}
.lines li + li{border-top:1px solid var(--lp-hair-faint)}
.row{display:grid;grid-template-columns:52px minmax(0,1fr);column-gap:16px;row-gap:6px;align-items:baseline;width:100%;min-height:var(--lp-hit);padding:12px var(--lp-pad) 13px calc(var(--lp-pad) - 2px);text-align:left;background:transparent;border:0;border-left:2px solid transparent;border-radius:0;cursor:pointer;transition:background-color var(--lp-hover)}
.row:hover{background:rgb(var(--lp-cream) / .04)}
.row:focus-visible{outline-offset:-4px}
.row[aria-current="true"]{border-left-color:var(--lp-l1);background:linear-gradient(90deg,rgb(var(--lp-lamp) / .08),transparent 80%)}
.r-tc{font-family:var(--lp-mono);font-size:12px;line-height:1.5;color:var(--lp-c2)}
.r-ln{font-family:var(--lp-display);font-optical-sizing:auto;font-style:italic;font-weight:350;font-size:16.5px;line-height:1.3;letter-spacing:-.005em;color:var(--lp-c0);text-wrap:pretty}
.r-x{grid-column:2;display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;letter-spacing:.01em;line-height:1.2;color:var(--lp-c0)}
.r-x .w{font-weight:500;color:var(--lp-c2)}
.empty{padding:2px var(--lp-pad) 18px;font-size:15px;line-height:1.5;color:var(--lp-c2)}
.keymap{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:0 32px;padding:0 var(--lp-pad) 16px}
.keymap div{display:grid;grid-template-columns:76px minmax(0,1fr);align-items:baseline;gap:12px;padding:9px 0;border-top:1px solid var(--lp-hair-faint)}
.keymap dt{display:flex;gap:4px}
.keymap dd{font-size:13.5px;color:var(--lp-c1)}
kbd{display:inline-block;min-width:24px;padding:3px 6px;font-family:var(--lp-mono);font-size:12px;line-height:1.2;text-align:center;color:var(--lp-c0);border:1px solid var(--lp-hair-strong)}

/* full screen: the picture takes the room, the controls stay */
:host(:fullscreen){background:var(--lp-n0)}
:host(:fullscreen) .player{height:100%;border:0}
:host(:fullscreen) .stage{flex:1 1 auto;min-height:0;aspect-ratio:auto}
:host(:fullscreen) .panel{max-height:30vh;overflow-y:auto}

/* middle widths: icons carry the description controls; their words stay as names */
@container lull (max-width:1099px){
  .lbl{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  .btn.has-lbl{padding:0}
}
/* narrow: two rows, the programme over the descriptions, in reading (and tab) order */
@container lull (max-width:799px){
  .left,.right{flex:1 1 100%}
  .time{margin-right:auto}
  .modewrap{flex:1 1 auto;min-width:0}
  .mode{width:100%;justify-content:space-between}
  .menu{left:0;right:auto}
  /* the menu opens over the picture: each mode shows its name, and only the chosen one explains itself */
  .opt:not([data-checked]) .o-desc{display:none}
  .opt{padding-top:10px;padding-bottom:10px}
}
@container lull (max-width:639px){
  :host{--lp-cue:17px}
  .row{grid-template-columns:44px minmax(0,1fr);column-gap:12px}
  .n-title{font-size:20px}
  .n-body{font-size:15px}
}
@container lull (max-width:519px){
  .volume{display:none}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition:none!important;animation:none!important}
}
@media (forced-colors:active){
  .track,.fill,.spans i,.spans b,.head{forced-color-adjust:none}
  .play{border:1px solid ButtonText}
}
`;

const KEYS: [string[], string][] = [
  [["K", "Space"], "Play or pause"],
  [["←", "→"], "Back or ahead 5 s"],
  [["↑", "↓"], "Volume"],
  [["M"], "Description mode"],
  [["C"], "Captions"],
  [["T"], "Transcript"],
  [["F"], "Full screen"],
  [["<", ">"], "Slower or faster"],
];

const kbdRows = KEYS.map(([keys, what]) => `<div><dt>${keys.map((k) => `<kbd>${k}</kbd>`).join("")}</dt><dd>${what}</dd></div>`).join("");

export const TEMPLATE = /* html */ `
<div class="player" part="player" role="region" aria-label="Described video">
  <div class="stage" tabindex="-1">
    <video playsinline preload="metadata"></video>
    <div class="notice" hidden>
      <p class="n-title"></p>
      <p class="n-body"></p>
      <p class="n-url"></p>
    </div>
  </div>
  <div class="band" data-state="idle" aria-hidden="true">
    <p class="hold" hidden><span>${ICON.extended}Extended</span><span class="w"></span></p>
    <p class="line" hidden></p>
    <p class="idle"></p>
  </div>
  <div class="rail">
    <div class="track"><div class="fill"></div><div class="spans"></div><div class="head"></div></div>
    <input class="seek" type="range" min="0" max="0" step="any" value="0" aria-label="Seek" aria-keyshortcuts="ArrowLeft ArrowRight Home End" disabled>
  </div>
  <div class="bar">
    <div class="left" role="group" aria-label="Playback">
      <button class="play" type="button" aria-keyshortcuts="k Space">${ICON.play}<span class="sr-only">Play</span></button>
      <span class="time" aria-hidden="true"><span class="now">0:00</span><span class="of"> / <span class="dur">0:00</span></span></span>
      <div class="vol">
        <button class="btn icon mute" type="button" aria-label="Mute">${ICON.voice}</button>
        <input class="volume" type="range" min="0" max="1" step="0.05" value="1" aria-label="Volume" aria-keyshortcuts="ArrowUp ArrowDown">
      </div>
      <button class="btn speed" type="button" aria-keyshortcuts="< >"><span class="sr-only">Speed </span><span class="v">1×</span></button>
      <button class="btn icon fs" type="button" aria-label="Full screen" aria-keyshortcuts="f">${ICON.enter}</button>
    </div>
    <div class="right" role="group" aria-label="Descriptions">
      <div class="modewrap">
        <button class="btn mode" type="button" aria-expanded="false" aria-controls="menu" aria-keyshortcuts="m"><span class="k lbl">Description mode</span> <span class="v">Mixed audio</span>${ICON.caret}</button>
        <div class="menu" id="menu" hidden>
          <div class="group" role="radiogroup" aria-labelledby="menu-title">
            <p class="menu-title" id="menu-title">Description mode</p>
          </div>
        </div>
      </div>
      <button class="btn has-lbl cc" type="button" aria-pressed="false" aria-keyshortcuts="c" hidden>${ICON.captions}<span class="lbl">Captions</span></button>
      <button class="btn has-lbl tx" type="button" aria-expanded="false" aria-controls="transcript" aria-keyshortcuts="t">${ICON.transcript}<span class="lbl">Transcript</span></button>
      <button class="btn has-lbl keys" type="button" aria-expanded="false" aria-controls="keys" aria-keyshortcuts="?">${ICON.keys}<span class="lbl">Keys</span></button>
    </div>
  </div>
  <p class="meta"></p>
  <section class="panel" id="transcript" aria-label="Descriptions transcript" hidden>
    <p class="p-head"><span>Descriptions</span><span class="count"></span></p>
    <ol class="lines"></ol>
    <p class="empty" hidden></p>
  </section>
  <section class="panel" id="keys" aria-label="Keyboard shortcuts" hidden>
    <p class="p-head"><span>Keys, while the player has focus</span></p>
    <dl class="keymap">${kbdRows}</dl>
  </section>
  <div class="sr-only" aria-live="polite" aria-atomic="true" data-live="status"></div>
  <div class="sr-only" aria-live="polite" aria-atomic="true" data-live="cue"></div>
  <audio preload="none" hidden></audio>
</div>
`;
