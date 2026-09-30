"use strict";

// clearleaf editor. The server holds the document; every edit is one
// operation posted to /api/op, and the reply is the new document state.

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (sel) => document.querySelector(sel);
const PALETTE = ["#f2d10a", "#2e7d5a", "#1f5fd1", "#c0352b", "#1c2521", "#8a4fd1"];
const TOOL_COLOR = { highlight: 0, underline: 2, strikeout: 3, ink: 2, rect: 3, ellipse: 3, line: 3, freetext: 4, note: 0 };
const ZOOMS = [0.5, 0.67, 0.75, 0.9, 1, 1.25, 1.5, 2, 3];
const HINTS = {
  select: "Click text to edit it, drag it to move it, or click an empty spot to type",
  highlight: "Drag across text to highlight it", underline: "Drag across text to underline it",
  strikeout: "Drag across text to strike it out", ink: "Draw with the mouse", rect: "Drag to draw a rectangle",
  ellipse: "Drag to draw an ellipse", line: "Drag to draw a line", note: "Click where the comment goes",
  freetext: "Drag to place a text box", image: "Drag the area where the image goes",
  redact: "Drag over what should be removed for good",
};
const FAMILIES = { sans: "Helvetica, Arial, sans-serif", serif: '"Times New Roman", Times, serif', mono: '"Courier New", Courier, monospace' };
const STANDARD = { sans: ["helv", "hebo", "heit", "hebi"], serif: ["tiro", "tibo", "tiit", "tibi"], mono: ["cour", "cobo", "coit", "cobi"] };
const MARKUP = new Set(["highlight", "underline", "strikeout"]);

const ui = {
  state: null,
  tool: "select",
  colors: { ...TOOL_COLOR },
  zoom: "fit",
  scale: 1,
  current: 0,
  selectedAnnot: null,
  selectedPages: new Set(),
  lines: new Map(), // page key -> text lines
  blanks: new Map(), // page key -> blanks to fill in
  patches: [], // edited lines shown until the page's new render arrives
  busy: 0,
};

// -- server ------------------------------------------------------------------------

function url(path, params = {}) {
  const q = new URLSearchParams({ ...params, t: TOKEN });
  return `${path}?${q}`;
}

async function request(path, { body, raw, method = "POST" } = {}) {
  ui.busy++;
  setStatus();
  try {
    const res = await fetch(url(path), {
      method,
      headers: { "X-Token": TOKEN, ...(raw ? { "Content-Type": "application/octet-stream" } : { "Content-Type": "application/json" }) },
      body: raw ?? (body === undefined ? undefined : JSON.stringify(body)),
    });
    const data = await res.json().catch(() => ({ error: `The editor stopped responding (${res.status}). Is it still running?` }));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  } catch (err) {
    toast(err.message === "Failed to fetch" ? "Can't reach the editor. Run clearleaf edit again to reopen the file." : err.message, true);
    throw err;
  } finally {
    ui.busy--;
    setStatus();
  }
}

async function op(operation) {
  const state = await request("/api/op", { body: operation });
  ui.selectedAnnot = null;
  setState(state);
  return state;
}

async function upload(file) {
  const buf = await file.arrayBuffer();
  const { id } = await request("/api/blob", { raw: buf });
  return id;
}

// -- geometry: page coordinates (points, top-left, unrotated) <-> view -------------------

function viewSize(p) {
  const turned = p.rotation % 180 !== 0;
  return turned ? [p.height, p.width] : [p.width, p.height];
}

function toView(p, x, y) {
  const W = p.width, H = p.height;
  switch (p.rotation) {
    case 90: return [H - y, x];
    case 180: return [W - x, H - y];
    case 270: return [y, W - x];
    default: return [x, y];
  }
}

function toPage(p, vx, vy) {
  const W = p.width, H = p.height;
  switch (p.rotation) {
    case 90: return [vy, H - vx];
    case 180: return [W - vx, H - vy];
    case 270: return [W - vy, vx];
    default: return [vx, vy];
  }
}

function rectToView(p, r) {
  const [a, b] = [toView(p, r[0], r[1]), toView(p, r[2], r[3])];
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

function rectToPage(p, a, b) {
  const [x0, y0] = toPage(p, a[0], a[1]);
  const [x1, y1] = toPage(p, b[0], b[1]);
  return [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
}

function place(el, r) {
  const s = ui.scale;
  Object.assign(el.style, { left: `${r[0] * s}px`, top: `${r[1] * s}px`, width: `${(r[2] - r[0]) * s}px`, height: `${(r[3] - r[1]) * s}px` });
}

function hex(color) {
  const n = parseInt(color.slice(1), 16);
  return [(n >> 16 & 255) / 255, (n >> 8 & 255) / 255, (n & 255) / 255].map((v) => Math.round(v * 1000) / 1000);
}

// -- state and rendering ------------------------------------------------------------------

function setState(state) {
  ui.state = state;
  $("#filename").textContent = state.name;
  document.title = `${state.dirty ? "• " : ""}${state.name} — clearleaf`;
  $("#undo").disabled = !state.can_undo;
  $("#redo").disabled = !state.can_redo;
  ui.current = Math.min(ui.current, state.pages.length - 1);
  for (const i of [...ui.selectedPages]) if (i >= state.pages.length) ui.selectedPages.delete(i);
  computeScale();
  if (ui.dragging) ui.renderPending = true;  // don't pull the page out from under a drag
  else renderPages();
  renderThumbs();
  renderRedactBar();
  setStatus();
}

function endDrag() {
  ui.dragging = false;
  if (ui.renderPending) {
    ui.renderPending = false;
    renderPages();
  }
}

function setStatus() {
  const el = $("#status");
  if (!ui.state) return;
  el.textContent = ui.busy ? "Saving edit…" : ui.state.dirty ? "Edited" : "No changes";
  el.className = `status${ui.busy ? " busy" : ui.state.dirty ? " dirty" : ""}`;
}

function computeScale() {
  const pages = ui.state.pages;
  if (ui.zoom === "fit") {
    const widest = Math.max(...pages.map((p) => viewSize(p)[0]));
    const avail = $("#desk").clientWidth - 64;
    ui.scale = Math.max(0.3, Math.min(3, avail / widest));
  } else {
    ui.scale = Number(ui.zoom) * 96 / 72;
  }
  $("#zoom-level").textContent = `${Math.round(ui.scale * 72 / 96 * 100)}%`;
}

function zoomBy(dir) {
  const now = ui.scale * 72 / 96;
  const next = dir > 0 ? ZOOMS.find((z) => z > now + 0.01) : [...ZOOMS].reverse().find((z) => z < now - 0.01);
  if (next === undefined) return;
  ui.zoom = next;
  computeScale();
  renderPages();
}

function renderDpi() {
  return Math.round(Math.min(144, 72 * ui.scale * (window.devicePixelRatio || 1)));
}

// page (by identity, not position) -> URL of the image last shown for it, so a rebuilt page keeps
// showing the previous render until the new one has loaded
const shown = new Map();
const identity = (p) => p.key.split("-").slice(0, 2).join("-");

let pageObserver;
let currentObserver;

function renderPages() {
  const container = $("#pages");
  const desk = $("#desk");
  const keep = desk.scrollTop;
  // an inline editor being typed in survives the rebuild
  const open = [...container.querySelectorAll(".inline-editor, .inline-hint, .inplace-wrap, .inplace.new, .ghost, .hole")].map((n) => ({
    node: n, page: n.closest(".page")?.dataset.index, focused: n.contains(document.activeElement) }));
  open.forEach((o) => { o.node.dataset.moving = "1"; });
  container.replaceChildren();
  pageObserver?.disconnect();
  currentObserver?.disconnect();
  pageObserver = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) loadPageImage(e.target);
  }, { root: desk, rootMargin: "600px 0px" });
  const ratios = new Map();
  currentObserver = new IntersectionObserver((entries) => {
    for (const e of entries) ratios.set(Number(e.target.dataset.index), e.intersectionRatio);
    let best = ui.current, bestRatio = -1;
    for (const [i, r] of ratios) if (r > bestRatio) { best = i; bestRatio = r; }
    if (best !== ui.current) { ui.current = best; markCurrent(); }
  }, { root: desk, threshold: [0, 0.25, 0.5, 0.75, 1] });

  ui.state.pages.forEach((p, i) => {
    const [vw, vh] = viewSize(p);
    const el = document.createElement("div");
    el.className = "page loading";
    el.dataset.index = i;
    el.style.width = `${vw * ui.scale}px`;
    el.style.height = `${vh * ui.scale}px`;
    const img = document.createElement("img");
    img.alt = `Page ${i + 1}`;
    img.decoding = "async";
    const previous = shown.get(identity(p));
    if (previous && previous.size === `${vw}x${vh}`) {
      img.src = previous.src;
      el.classList.remove("loading");
    }
    const layer = document.createElement("div");
    layer.className = "layer";
    const label = document.createElement("div");
    label.className = "page-label";
    label.textContent = `${i + 1} of ${ui.state.pages.length}`;
    el.append(img, layer, label);
    buildOverlays(el, p, i);
    el.addEventListener("pointerdown", (e) => onPagePointerDown(e, el, p, i));
    container.append(el);
    pageObserver.observe(el);
    currentObserver.observe(el);
  });
  desk.scrollTop = keep;
  for (const o of open) {
    const layer = container.querySelector(`.page[data-index="${o.page}"] .layer`);
    if (layer) layer.append(o.node);
    delete o.node.dataset.moving;
    if (o.focused) (o.node.matches("input, textarea") ? o.node : o.node.querySelector("input"))?.focus();
  }
  markCurrent();
}

function loadPageImage(el) {
  const i = Number(el.dataset.index);
  const p = ui.state.pages[i];
  const [vw, vh] = viewSize(p);
  const img = el.querySelector("img");
  const src = url("/api/render", { page: i, dpi: renderDpi(), v: p.key });
  if (img.dataset.want === src) return;
  img.dataset.want = src;
  const show = (s, final) => {
    if (img.dataset.want !== src || !el.isConnected) return;
    if (!final && img.dataset.final === src) return;
    img.src = s;
    if (final) {
      img.dataset.final = src;
      dropPatches(identity(p));
    }
    el.classList.remove("loading");
    shown.set(identity(p), { src: s, size: `${vw}x${vh}` });
  };
  const fetchImage = (s, final) => {
    const pre = new Image();
    pre.onload = () => show(s, final);
    pre.src = s;
  };
  if (!shown.has(identity(p)) || shown.get(identity(p)).size !== `${vw}x${vh}`) {
    fetchImage(url("/api/render", { page: i, dpi: 36, v: p.key }), false);  // quick preview
  }
  fetchImage(src, true);
}

function markCurrent() {
  document.querySelectorAll(".page.current, .thumb.current").forEach((n) => n.classList.remove("current"));
  document.querySelector(`.page[data-index="${ui.current}"]`)?.classList.add("current");
  const thumb = document.querySelector(`.thumb[data-index="${ui.current}"]`);
  thumb?.classList.add("current");
}

function dropPatches(id) {
  ui.patches = ui.patches.filter((x) => {
    if (x.id !== id || x.busy) return true;
    x.node.remove();
    return false;
  });
}

function buildOverlays(el, p, i) {
  const layer = el.querySelector(".layer");
  for (const a of p.annots) {
    const hit = document.createElement("div");
    hit.className = "annot-hit" + (a.type === "Redact" ? " redact" : "");
    place(hit, rectToView(p, a.rect));
    hit.title = a.type === "Redact" ? "Marked for redaction" : a.content || a.type;
    hit.addEventListener("pointerdown", (e) => {
      if (ui.tool !== "select" || e.button !== 0 || e.target.closest(".annot-tip")) return;
      e.stopPropagation();
      e.preventDefault();
      pressToMove(e, el, p, a.rect, {
        click: () => selectAnnot(hit, i, a),
        move: (dx, dy) => op({ op: "move_annot", page: i, id: a.id, dx, dy }),
      });
    });
    layer.append(hit);
  }
  for (const f of p.fields) layer.append(fieldInput(p, i, f));
  if (ui.tool === "select") addTextLines(el, p, i);
}

function fieldInput(p, i, f) {
  let input;
  const r = rectToView(p, f.rect);
  const send = (value) => op({ op: "field", page: i, name: f.name, xref: f.xref, value }).catch(() => {});
  if (f.type === "checkbox" || f.type === "radiobutton") {
    input = document.createElement("input");
    input.type = f.type === "checkbox" ? "checkbox" : "radio";
    input.name = `${i}:${f.name}`;
    input.checked = f.type === "checkbox" ? !!f.value : f.value === f.on_state && !!f.value;
    input.addEventListener("change", () => send(f.type === "checkbox" ? input.checked : true));
    const size = Math.min(r[2] - r[0], r[3] - r[1]);
    r[2] = r[0] + size;
    r[3] = r[1] + size;
  } else if (f.type === "combobox" || f.type === "listbox") {
    input = document.createElement("select");
    input.append(new Option("", ""));
    for (const o of f.options || []) input.append(new Option(o, o, false, o === f.value));
    input.addEventListener("change", () => send(input.value));
  } else {
    input = document.createElement(f.multiline ? "textarea" : "input");
    input.value = f.value || "";
    input.addEventListener("change", () => send(input.value));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !f.multiline) input.blur(); });
    input.style.fontSize = `${Math.max(9, Math.min(14, (r[3] - r[1]) * 0.7)) * ui.scale}px`;
  }
  input.className = "field";
  input.title = f.name;
  input.setAttribute("aria-label", f.name);
  input.disabled = f.readonly;
  place(input, r);
  return input;
}

function selectAnnot(hit, i, a) {
  document.querySelectorAll(".annot-hit.selected").forEach((n) => { n.classList.remove("selected"); n.replaceChildren(); });
  ui.selectedAnnot = { page: i, id: a.id };
  hit.classList.add("selected");
  const tip = document.createElement("div");
  tip.className = "annot-tip";
  const label = a.type === "Redact" ? "Marked for redaction" : labelFor(a.type);
  const text = document.createElement("span");
  text.textContent = a.content ? `${label}: ${a.content}` : label;
  const del = document.createElement("button");
  del.textContent = a.type === "Redact" ? "Remove mark" : "Delete";
  del.addEventListener("pointerdown", (e) => e.stopPropagation());
  del.addEventListener("click", () => deleteSelectedAnnot());
  tip.append(text, del);
  hit.append(tip);
}

function labelFor(type) {
  return { Highlight: "Highlight", Underline: "Underline", StrikeOut: "Strikeout", Squiggly: "Squiggly underline",
    Text: "Comment", FreeText: "Text box", Ink: "Drawing", Square: "Rectangle", Circle: "Ellipse", Line: "Line", Link: "Link", Stamp: "Stamp" }[type] || type;
}

function deleteSelectedAnnot() {
  const s = ui.selectedAnnot;
  if (s) op({ op: "delete_annot", page: s.page, id: s.id }).catch(() => {});
}

// -- text lines (for marking and editing text) ---------------------------------------------

async function linesFor(i) {
  const p = ui.state.pages[i];
  if (!ui.lines.has(p.key)) {
    const res = await fetch(url("/api/text", { page: i }), { headers: { "X-Token": TOKEN } });
    ui.lines.set(p.key, res.ok ? await res.json() : []);
  }
  return ui.lines.get(p.key);
}

async function blanksFor(i) {
  const p = ui.state.pages[i];
  if (!ui.blanks.has(p.key)) {
    const res = await fetch(url("/api/blanks", { page: i }), { headers: { "X-Token": TOKEN } });
    ui.blanks.set(p.key, res.ok ? await res.json() : []);
  }
  return ui.blanks.get(p.key);
}

async function addTextLines(el, p, i) {
  const [lines, blanks] = await Promise.all([linesFor(i), blanksFor(i)]);
  if (ui.tool !== "select" || !el.isConnected) return;
  const layer = el.querySelector(".layer");
  const nodes = [];
  for (const line of lines) {
    if (line.hidden) continue;
    const box = document.createElement("div");
    box.className = "textline" + (line.editable ? "" : " locked");
    if (!line.editable) box.title = "Right-to-left text can't be edited here yet";
    place(box, rectToView(p, line.bbox));
    box.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      if (!line.editable) return;
      pressToMove(e, el, p, line.bbox, {
        click: () => editInPlace(el, p, i, line, pointIn(e, el, p).page[0]),
        move: (dx, dy) => op({ op: "move_text", page: i, rect: line.bbox, old: line.text, dx, dy }),
      });
    });
    nodes.push(box);
  }
  for (const blank of blanks) {
    // blanks lie on top of the underscores or dots they are made of
    const box = document.createElement("div");
    box.className = `blank ${blank.kind}`;
    box.title = blank.kind === "check" ? "Click to tick" : "Click to fill in";
    place(box, rectToView(p, blank.rect));
    box.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.stopPropagation();
      e.preventDefault();
      fillBlank(el, p, i, blank);
    });
    nodes.push(box);
  }
  layer.prepend(...nodes);  // under annotations and form fields
}

function fillBlank(el, p, i, blank) {
  closeEditors();
  const r = blank.rect;
  if (blank.kind === "check") {
    const w = blank.size * 0.667;  // "X" in Helvetica
    op({ op: "text", page: i, point: [(r[0] + r[2]) / 2 - w / 2, blank.baseline], text: "X", fontsize: blank.size,
      fontname: "helv", color: [0, 0, 0] }).catch(() => {});
    return;
  }
  typeNewText(el, p, i, [r[0] + 2, blank.baseline], {
    baseline: blank.baseline, size: blank.size, style: { family: "sans", bold: false, italic: false },
    color: "#000000", maxWidth: r[2] - r[0] - 2, blank: true });
}

// -- moving things by dragging ---------------------------------------------------------

function ghostOf(el, r) {
  // a piece of the page image, to drag around
  const s = ui.scale;
  const img = el.querySelector("img");
  const g = document.createElement("div");
  g.className = "ghost";
  place(g, r);
  Object.assign(g.style, { backgroundImage: `url("${img.src}")`, backgroundSize: `${el.clientWidth}px ${el.clientHeight}px`,
    backgroundPosition: `${-r[0] * s}px ${-r[1] * s}px` });
  return g;
}

function holeOver(el, r) {
  const h = document.createElement("div");
  h.className = "hole";
  place(h, [r[0] - 1, r[1] - 1, r[2] + 1, r[3] + 1]);
  h.style.background = paperColor(el, r);
  return h;
}

function pressToMove(e, el, p, pageRect, { click, move }) {
  // a click, or a drag that moves what was pressed
  const start = [e.clientX, e.clientY];
  const view = rectToView(p, pageRect);
  let ghost = null, hole = null;
  const layer = el.querySelector(".layer");
  const onMove = (ev) => {
    const dx = ev.clientX - start[0], dy = ev.clientY - start[1];
    if (!ghost && Math.hypot(dx, dy) < 5) return;
    if (!ghost) {
      closeEditors();
      hole = holeOver(el, view);
      ghost = ghostOf(el, view);
      layer.append(hole, ghost);
      ui.dragging = true;
    }
    ghost.style.transform = `translate(${dx}px, ${dy}px)`;
  };
  const onUp = async (ev) => {
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
    if (!ghost) { click(); return; }
    const s = ui.scale;
    const [vdx, vdy] = [(ev.clientX - start[0]) / s, (ev.clientY - start[1]) / s];
    // the move on the unrotated page
    const a = toPage(p, 0, 0), b = toPage(p, vdx, vdy);
    const patches = [hole, ghost].map((node) => ({ id: identity(p), node, busy: true }));
    ui.patches.push(...patches);
    endDrag();
    try {
      await move(b[0] - a[0], b[1] - a[1]);
      patches.forEach((x) => { x.busy = false; });
    } catch {
      hole.remove();
      ghost.remove();
      ui.patches = ui.patches.filter((x) => !patches.includes(x));
    }
  };
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
}

// -- editing text where it is -----------------------------------------------------------

const measurer = document.createElement("canvas").getContext("2d");

function cssFont(style, px) {
  return `${style.italic ? "italic " : ""}${style.bold ? 700 : 400} ${px}px ${FAMILIES[style.family] || FAMILIES.sans}`;
}

function paperColor(el, r) {
  // the page's color just around a box (view points), so what's put there sits on the same paper
  const img = el.querySelector("img");
  if (!img?.complete || !img.naturalWidth) return "#fff";
  const k = img.naturalWidth / (el.clientWidth / ui.scale);
  const x0 = Math.max(0, Math.floor((r[0] - 2) * k)), x1 = Math.min(img.naturalWidth, Math.ceil((r[2] + 2) * k));
  const y0 = Math.max(0, Math.floor((r[1] - 2) * k)), y1 = Math.min(img.naturalHeight, Math.ceil((r[3] + 2) * k));
  if (x1 - x0 < 2 || y1 - y0 < 2) return "#fff";
  const c = document.createElement("canvas");
  c.width = x1 - x0;
  c.height = y1 - y0;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  try {
    ctx.drawImage(img, x0, y0, c.width, c.height, 0, 0, c.width, c.height);
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    const counts = new Map();
    for (let y = 0; y < c.height; y++) {
      for (let x = 0; x < c.width; x++) {
        if (y > 0 && y < c.height - 1 && x > 0 && x < c.width - 1) continue;  // the border only
        const o = (y * c.width + x) * 4;
        const key = ((d[o] >> 3) << 10) | ((d[o + 1] >> 3) << 5) | (d[o + 2] >> 3);
        const seen = counts.get(key);
        if (seen) seen.n++;
        else counts.set(key, { n: 1, rgb: `rgb(${d[o]}, ${d[o + 1]}, ${d[o + 2]})` });
      }
    }
    return [...counts.values()].sort((a, b) => b.n - a.n)[0].rgb;
  } catch {
    return "#fff";
  }
}

function closeEditors() {
  document.querySelectorAll(".inplace:not(.pending), .inline-editor").forEach((n) => n.blur());
}

function estimateCarets(before, carets, after, font) {
  // where the characters fall until the real preview arrives: kept text shifts by what was typed
  let a = 0;
  while (a < before.length && a < after.length && before[a] === after[a]) a++;
  let z = 0;
  while (z < before.length - a && z < after.length - a && before[before.length - 1 - z] === after[after.length - 1 - z]) z++;
  measurer.font = font;
  const out = carets.slice(0, a + 1);
  let x = carets[a];
  for (let k = a; k < after.length - z; k++) {
    x += measurer.measureText(after[k]).width / ui.scale;
    out.push(x);
  }
  const shift = x - carets[before.length - z];
  for (let k = before.length - z + 1; k <= before.length; k++) out.push(carets[k] + shift);
  return out;
}

function editInPlace(el, p, i, line, atX) {
  if (p.rotation !== 0 || line.angle !== 0 || !line.carets) return editLineBox(el, p, i, line);
  closeEditors();
  const s = ui.scale;
  const b = line.bbox;
  const px = line.size * s;
  const font = cssFont(line.font, px);
  const layer = el.querySelector(".layer");

  // what is seen: the page as clearleaf draws it with the edit made; what is typed into: an invisible input
  const wrap = document.createElement("div");
  wrap.className = "inplace-wrap";
  const shot = document.createElement("img");
  shot.className = "preview";
  shot.alt = "";
  shot.hidden = true;
  const caret = document.createElement("div");
  caret.className = "caret";
  const sel = document.createElement("div");
  sel.className = "selection";
  const input = document.createElement("input");
  input.className = "inplace";
  input.value = line.text;
  input.spellcheck = false;
  input.setAttribute("aria-label", "Edit this line");
  const pad = 3;
  Object.assign(input.style, { font, left: `${b[0] * s - pad}px`, top: `${b[1] * s - pad}px`,
    height: `${(b[3] - b[1]) * s + 2 * pad}px` });
  wrap.append(shot, sel, caret, input);
  layer.append(wrap);

  let text = line.text, carets = line.carets.slice(), seq = 0, shown = 0;
  const draw = () => {
    const n = carets.length - 1;
    const a = Math.min(input.selectionStart ?? 0, n), z = Math.min(input.selectionEnd ?? 0, n);
    const x0 = carets[a] * s, x1 = carets[z] * s;
    Object.assign(caret.style, { left: `${x1}px`, top: `${b[1] * s}px`, height: `${(b[3] - b[1]) * s}px` });
    caret.hidden = a !== z;
    caret.classList.remove("blink");
    void caret.offsetWidth;
    caret.classList.add("blink");
    Object.assign(sel.style, { left: `${Math.min(x0, x1)}px`, width: `${Math.abs(x1 - x0)}px`, top: `${b[1] * s}px`,
      height: `${(b[3] - b[1]) * s}px` });
    sel.hidden = a === z;
    input.style.width = `${Math.max(carets[n] - b[0], 20) * s + 2 * pad}px`;
  };
  const refresh = async () => {
    const mine = ++seq;
    const wanted = input.value;
    try {
      const res = await fetch(url("/api/preview"), { method: "POST", headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify({ page: i, rect: b, old: line.text, text: wanted, dpi: renderDpi() * 2 }) });
      const data = await res.json();
      if (!res.ok || mine < shown) return;
      shown = mine;
      const c = data.clip;
      await new Promise((ok) => { shot.onload = ok; shot.onerror = ok; shot.src = data.image; });
      Object.assign(shot.style, { left: `${c[0] * s}px`, top: `${c[1] * s}px`, width: `${(c[2] - c[0]) * s}px`,
        height: `${(c[3] - c[1]) * s}px` });
      shot.hidden = false;
      if (data.carets && wanted === input.value) { carets = data.carets; draw(); }
    } catch { /* the estimate stays */ }
  };
  input.addEventListener("input", () => {
    carets = estimateCarets(text, carets, input.value, font);
    text = input.value;
    draw();
    refresh();
  });
  const onSelect = () => requestAnimationFrame(draw);
  input.addEventListener("keyup", onSelect);
  input.addEventListener("select", onSelect);
  document.addEventListener("selectionchange", onSelect);

  // clicks and drags inside the line place the caret by where the characters really are
  const indexAt = (ev) => nearestCaret({ carets }, pointIn(ev, el, p).page[0]);
  input.addEventListener("pointerdown", (ev) => {
    ev.stopPropagation();
    ev.preventDefault();
    input.focus();
    const from = ev.shiftKey ? input.selectionStart : indexAt(ev);
    const to = indexAt(ev);
    input.setSelectionRange(Math.min(from, to), Math.max(from, to));
    draw();
    const moveSel = (mv) => {
      const k = indexAt(mv);
      input.setSelectionRange(Math.min(from, k), Math.max(from, k), k < from ? "backward" : "forward");
      draw();
    };
    const stop = () => { window.removeEventListener("pointermove", moveSel); window.removeEventListener("pointerup", stop); };
    window.addEventListener("pointermove", moveSel);
    window.addEventListener("pointerup", stop);
  });
  input.addEventListener("dblclick", () => {
    const v = input.value, k = input.selectionStart;
    let a = k, z = k;
    while (a > 0 && /\w/.test(v[a - 1])) a--;
    while (z < v.length && /\w/.test(v[z])) z++;
    input.setSelectionRange(a, z);
    draw();
  });

  let done = false;
  const finish = async (save, then) => {
    if (done) return;
    done = true;
    document.removeEventListener("selectionchange", onSelect);
    const value = input.value;
    caret.remove();
    sel.remove();
    if (!save || value === line.text) {
      wrap.remove();
      then?.();
      return;
    }
    input.readOnly = true;
    wrap.classList.add("pending");
    input.blur();  // shortcuts work again at once
    const patch = { id: identity(p), node: wrap, busy: true };
    ui.patches.push(patch);
    try {
      await op({ op: "edit_text", page: i, rect: b, old: line.text, text: value });
      patch.busy = false;
      then?.();
    } catch {
      wrap.remove();
      ui.patches = ui.patches.filter((x) => x !== patch);
    }
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") finish(false);
    else if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      const x = carets[Math.min(input.selectionStart, carets.length - 1)];
      finish(true, () => moveToLine(i, line, e.key === "ArrowUp" ? -1 : 1, x));
    } else onSelect();
  });
  input.addEventListener("blur", () => { if (!wrap.dataset.moving && !input.readOnly) finish(true); });
  input.focus();
  const at = nearestCaret(line, atX);
  input.setSelectionRange(at, at);
  draw();
}

function nearestCaret(line, x) {
  let best = line.carets.length - 1, dist = Infinity;
  line.carets.forEach((c, k) => { if (Math.abs(c - x) < dist) { dist = Math.abs(c - x); best = k; } });
  return best;
}

async function moveToLine(i, from, dir, x) {
  const lines = (await linesFor(i)).filter((l) => l.editable && !l.hidden && l.angle === 0 && l.carets);
  const candidates = lines.filter((l) => (dir < 0 ? l.baseline < from.baseline - 1 : l.baseline > from.baseline + 1)
    && l.bbox[0] - 20 < x && l.bbox[2] + 20 > x);
  if (!candidates.length) return;
  candidates.sort((a, b) => Math.abs(a.baseline - from.baseline) - Math.abs(b.baseline - from.baseline));
  const el = document.querySelector(`.page[data-index="${i}"]`);
  editInPlace(el, ui.state.pages[i], i, candidates[0], x);
}

function editLineBox(el, p, i, line) {
  // rotated text: edit it in a box next to the line
  const r = rectToView(p, line.bbox);
  inlineEditor(el, {
    rect: [r[0] - 2, r[1] - 2, Math.max(r[2] + 40, r[0] + 160), r[1] + line.size * 1.4],
    value: line.text,
    fontSize: line.size,
    multiline: false,
    hint: "Enter to change the line · Esc to cancel",
    commit: (text) => {
      if (text !== line.text) op({ op: "edit_text", page: i, rect: line.bbox, old: line.text, text }).catch(() => {});
    },
  });
}

async function typeNewText(el, p, i, at, given = null) {
  // a new line of text where the page was clicked, styled like the nearest line
  closeEditors();
  let style, size, colorHex, baseline;
  if (given) {
    ({ style, size, baseline } = given);
    colorHex = given.color;
  } else {
    const lines = (ui.lines.get(p.key) || []).filter((l) => !l.hidden && l.angle === 0 && l.baseline !== undefined);
    let near = null, dist = 60;
    for (const l of lines) {
      const d = Math.abs(l.baseline - at[1]) + Math.max(0, l.bbox[0] - at[0], at[0] - l.bbox[2]) * 0.2;
      if (d < dist) { dist = d; near = l; }
    }
    style = near ? near.font : { family: "sans", bold: false, italic: false };
    size = near ? Math.round(near.size * 10) / 10 : 11;
    colorHex = near ? near.color : "#000000";
    baseline = at[1] + size * 0.35;
  }
  if (p.rotation !== 0) {
    const [vx, vy] = toView(p, at[0], at[1]);
    inlineEditor(el, {
      rect: [vx, vy - size * 0.6, vx + 260, vy + size * 0.9], value: "", fontSize: size, multiline: false,
      hint: "Type · Enter to add · Esc to cancel",
      commit: (text) => { if (text.trim()) op({ op: "text", page: i, point: [at[0], baseline], text, fontsize: size }).catch(() => {}); },
    });
    return;
  }
  const s = ui.scale;
  const px = size * s;
  const font = cssFont(style, px);
  measurer.font = font;
  const m = measurer.measureText("Hg");
  const lh = px * 1.25;
  const asc = m.fontBoundingBoxAscent ?? px * 0.9, desc = m.fontBoundingBoxDescent ?? px * 0.22;
  const baselineOffset = (lh - (asc + desc)) / 2 + asc;
  const input = document.createElement("input");
  input.className = "inplace new" + (given?.blank ? " on-blank" : "");
  input.spellcheck = false;
  input.setAttribute("aria-label", "New text");
  Object.assign(input.style, { font, color: colorHex, lineHeight: `${lh}px`, height: `${lh}px`,
    left: `${at[0] * s}px`, top: `${baseline * s - baselineOffset}px` });
  const room = Math.min(given?.maxWidth ?? Infinity, el.clientWidth / s - at[0]);
  const fit = () => {
    measurer.font = font;
    input.style.width = `${Math.max(Math.min(measurer.measureText(input.value).width + 6, el.clientWidth - at[0] * s), given?.blank ? room * s : 0)}px`;
  };
  fit();
  input.addEventListener("input", fit);
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const text = input.value;
    if (!save || !text.trim()) { input.remove(); return; }
    input.readOnly = true;
    input.classList.add("pending");
    input.blur();  // shortcuts work again at once
    const patch = { id: identity(p), node: input, busy: true };
    ui.patches.push(patch);
    const fontname = STANDARD[style.family][(style.bold ? 1 : 0) + (style.italic ? 2 : 0)];
    try {
      await op({ op: "text", page: i, point: [at[0], baseline], text, fontsize: size, fontname, color: hex(colorHex) });
      patch.busy = false;
    } catch {
      input.remove();
      ui.patches = ui.patches.filter((x) => x !== patch);
    }
  };
  input.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") finish(false);
    else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      finish(true);
      if (given?.blank && e.key === "Tab") nextBlank(i, at, e.shiftKey ? -1 : 1);
    }
  });
  input.addEventListener("blur", () => { if (!input.dataset.moving) finish(true); });
  input.addEventListener("pointerdown", (e) => e.stopPropagation());
  el.querySelector(".layer").append(input);
  input.focus();
}

async function nextBlank(i, at, dir) {
  // Tab goes on to the next blank, as in a form
  const blanks = (await blanksFor(i)).filter((b) => b.kind !== "check");
  const order = blanks.map((b) => b.rect).sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const k = order.findIndex((r) => Math.abs(r[0] + 2 - at[0]) < 0.5 && at[1] >= r[1] - 2 && at[1] <= r[3] + 2);
  const target = order[k + dir];
  if (k < 0 || !target) return;
  const blank = blanks.find((b) => b.rect === target);
  const el = document.querySelector(`.page[data-index="${i}"]`);
  fillBlank(el, ui.state.pages[i], i, blank);
}

function markupRects(lines, a, b) {
  // text-flow selection from point a to point b (page coordinates)
  let [start, end] = a[1] <= b[1] ? [a, b] : [b, a];
  const top = start[1], bottom = end[1];
  const hit = lines.filter((l) => l.bbox[3] >= top && l.bbox[1] <= bottom).sort((l, m) => l.bbox[1] - m.bbox[1]);
  if (hit.length === 1) {
    const l = hit[0].bbox;
    const x0 = Math.max(l[0], Math.min(a[0], b[0])), x1 = Math.min(l[2], Math.max(a[0], b[0]));
    return x1 - x0 > 1 ? [[x0, l[1], x1, l[3]]] : [];
  }
  return hit.map((l, k) => {
    const [bx0, by0, bx1, by1] = l.bbox;
    const x0 = k === 0 ? Math.max(bx0, start[0]) : bx0;
    const x1 = k === hit.length - 1 ? Math.min(bx1, end[0]) : bx1;
    return [x0, by0, x1, by1];
  }).filter((r) => r[2] - r[0] > 1);
}

// -- drawing with the tools --------------------------------------------------------------

function pointIn(e, el, p) {
  const box = el.getBoundingClientRect();
  const vx = (e.clientX - box.left) / ui.scale, vy = (e.clientY - box.top) / ui.scale;
  return { view: [vx, vy], page: toPage(p, vx, vy) };
}

function color() {
  return PALETTE[ui.colors[ui.tool] ?? 4];
}

function onPagePointerDown(e, el, p, i) {
  if (e.button !== 0) return;
  if (e.target.closest(".field, .inline-editor, .inplace, .inplace-wrap, .annot-tip")) return;
  if (ui.current !== i) { ui.current = i; markCurrent(); }
  const tool = ui.tool;
  if (tool === "select") {
    const had = document.querySelector(".annot-hit.selected, .inplace-wrap:not(.pending), .inplace.new:not(.pending), .inline-editor")
      || document.activeElement?.classList.contains("field");
    document.querySelectorAll(".annot-hit.selected").forEach((n) => { n.classList.remove("selected"); n.replaceChildren(); });
    ui.selectedAnnot = null;
    e.preventDefault();
    if (had) { closeEditors(); document.activeElement?.blur(); return; }  // the first click only closes what was open
    typeNewText(el, p, i, pointIn(e, el, p).page);
    return;
  }
  e.preventDefault();
  const start = pointIn(e, el, p);
  const layer = el.querySelector(".layer");

  if (tool === "note") {
    const [vx, vy] = start.view;
    inlineEditor(el, {
      rect: [vx, vy, vx + 220, vy + 96],
      value: "",
      fontSize: 12,
      multiline: true,
      hint: "⌘Enter to add the comment · Esc to cancel",
      commit: (text) => { if (text.trim()) op({ op: "note", page: i, point: start.page, text }).catch(() => {}); },
    });
    return;
  }

  // listen right away; the page's text lines load in parallel
  let lines = MARKUP.has(tool) ? ui.lines.get(p.key) || [] : null;
  const linesReady = MARKUP.has(tool) ? linesFor(i).then((l) => { lines = l; return l; }) : null;
  const rubber = [];
  let svg = null, path = null;
  const points = [start.page];
  if (tool === "ink" || tool === "line") {
    svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("draft");
    path = document.createElementNS("http://www.w3.org/2000/svg", tool === "ink" ? "polyline" : "line");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", color());
    path.setAttribute("stroke-width", String(2 * ui.scale));
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.append(path);
    layer.append(svg);
  }
  const clearRubber = () => { rubber.splice(0).forEach((n) => n.remove()); };
  const addRubber = (r, cls = "") => {
    const n = document.createElement("div");
    n.className = `rubber ${cls}`;
    place(n, r);
    layer.append(n);
    rubber.push(n);
  };
  let current = start;
  const move = (ev) => {
    current = pointIn(ev, el, p);
    clearRubber();
    if (MARKUP.has(tool)) {
      const rects = lines.length ? markupRects(lines, start.page, current.page) : [rectToPage(p, start.view, current.view)];
      for (const r of rects) addRubber(rectToView(p, r), "mark");
    } else if (tool === "ink") {
      points.push(current.page);
      path.setAttribute("points", points.map((q) => toView(p, q[0], q[1]).map((v) => v * ui.scale).join(",")).join(" "));
    } else if (tool === "line") {
      const [a, b] = [start.view, current.view].map((v) => v.map((c) => c * ui.scale));
      path.setAttribute("x1", a[0]); path.setAttribute("y1", a[1]); path.setAttribute("x2", b[0]); path.setAttribute("y2", b[1]);
    } else {
      const r = [Math.min(start.view[0], current.view[0]), Math.min(start.view[1], current.view[1]),
        Math.max(start.view[0], current.view[0]), Math.max(start.view[1], current.view[1])];
      addRubber(r, tool === "ellipse" ? "ellipse" : tool === "redact" ? "redact" : "");
    }
  };
  const up = async () => {
    if (linesReady) await linesReady.catch(() => {});
    el.removeEventListener("pointermove", move);
    el.removeEventListener("pointerup", up);
    el.removeEventListener("pointercancel", up);
    endDrag();
    el = document.querySelector(`.page[data-index="${i}"]`) || el;  // the page may have been rebuilt
    const moved = Math.hypot(current.view[0] - start.view[0], current.view[1] - start.view[1]) > 3 / ui.scale;
    let rect = rectToPage(p, start.view, current.view);
    try {
      if (MARKUP.has(tool)) {
        const rects = lines.length ? markupRects(lines, start.page, current.page) : (moved ? [rect] : []);
        if (rects.length) await op({ op: tool, page: i, rects, color: hex(color()) });
        else if (!lines.length) toast("There's no selectable text on this page. Drag across the area to mark it.");
      } else if (tool === "ink") {
        if (points.length > 1) await op({ op: "ink", page: i, strokes: [points], color: hex(color()), width: 2 });
      } else if (tool === "line") {
        if (moved) await op({ op: "line", page: i, p1: start.page, p2: current.page, color: hex(color()) });
      } else if (tool === "freetext") {
        if (!moved) rect = [start.page[0], start.page[1], start.page[0] + 180, start.page[1] + 40];
        clearRubber();
        const r = rectToView(p, rect);
        inlineEditor(el, {
          rect: r, value: "", fontSize: 12, multiline: true, hint: "Type · ⌘Enter to add · Esc to cancel",
          commit: (text) => { if (text.trim()) op({ op: "freetext", page: i, rect, text, fontsize: 12, color: hex(color()) }).catch(() => {}); },
        });
        return;
      } else if (tool === "image") {
        if (!moved) rect = [start.page[0], start.page[1], start.page[0] + 150, start.page[1] + 150];
        const file = await pickFile("#file-image");
        if (file) await op({ op: "image", page: i, rect, blob: await upload(file) });
      } else if (moved && (tool === "rect" || tool === "ellipse")) {
        await op({ op: tool, page: i, rect, color: hex(color()) });
      } else if (moved && tool === "redact") {
        await op({ op: "redact_mark", page: i, rect });
      }
    } catch { /* shown as a toast */ }
    clearRubber();
    svg?.remove();
  };
  ui.dragging = true;
  el.setPointerCapture(e.pointerId);
  el.addEventListener("pointermove", move);
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
}

function inlineEditor(el, { rect, value, fontSize, multiline, hint, commit }) {
  document.querySelectorAll(".inline-editor, .inline-hint").forEach((n) => n.remove());
  const box = document.createElement(multiline ? "textarea" : "input");
  box.className = "inline-editor";
  box.value = value;
  box.style.fontSize = `${fontSize * ui.scale}px`;
  place(box, rect);
  const tip = document.createElement("div");
  tip.className = "inline-hint";
  tip.textContent = hint;
  tip.style.left = `${rect[0] * ui.scale}px`;
  tip.style.top = `${rect[3] * ui.scale + 4}px`;
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const text = box.value;
    box.remove();
    tip.remove();
    if (save) commit(text);
  };
  box.addEventListener("keydown", (e) => {
    e.stopPropagation();
    if (e.key === "Escape") finish(false);
    else if (e.key === "Enter" && (!multiline || e.metaKey || e.ctrlKey)) { e.preventDefault(); finish(true); }
  });
  box.addEventListener("blur", () => { if (!box.dataset.moving) finish(true); });
  box.addEventListener("pointerdown", (e) => e.stopPropagation());
  el.querySelector(".layer").append(box, tip);
  box.focus();  // at once, so nothing typed goes to the tool shortcuts
  box.select?.();
}

function pickFile(sel) {
  return new Promise((resolve) => {
    const input = $(sel);
    input.value = "";
    const done = () => { input.removeEventListener("change", done); resolve(input.files[0] || null); };
    input.addEventListener("change", done);
    input.click();
  });
}

// -- tools and colors ---------------------------------------------------------------------

function setTool(tool) {
  closeEditors();
  ui.tool = tool;
  document.body.dataset.tool = tool;
  document.querySelectorAll(".tool").forEach((b) => {
    b.classList.toggle("active", b.dataset.tool === tool);
    b.setAttribute("aria-pressed", String(b.dataset.tool === tool));
  });
  document.documentElement.style.setProperty("--tool", tool === "redact" ? "#d93b30" : tool in TOOL_COLOR ? color() : "#2f55ff");
  $(".color-pick").classList.toggle("off", !(tool in TOOL_COLOR));
  $("#swatches").hidden = true;
  renderSwatches();
  showHint(HINTS[tool]);
  if (ui.state) renderPages();
}

let hintTimer;
function showHint(text) {
  $("#hint").textContent = text || "";
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => { $("#hint").textContent = ""; }, 4500);
}

function renderSwatches() {
  const box = $("#swatches");
  box.replaceChildren();
  if (!(ui.tool in TOOL_COLOR)) return;
  PALETTE.forEach((c, k) => {
    const b = document.createElement("button");
    b.className = "swatch" + (ui.colors[ui.tool] === k ? " active" : "");
    b.style.background = c;
    b.setAttribute("aria-label", `Color ${k + 1}`);
    b.addEventListener("click", () => {
      ui.colors[ui.tool] = k;
      document.documentElement.style.setProperty("--tool", c);
      box.hidden = true;
      renderSwatches();
    });
    box.append(b);
  });
}

// -- page strip ---------------------------------------------------------------------------

function renderThumbs() {
  const list = $("#thumbs");
  list.replaceChildren();
  const pages = ui.state.pages;
  let dragFrom = null;
  pages.forEach((p, i) => {
    const [vw, vh] = viewSize(p);
    const li = document.createElement("li");
    li.className = "thumb";
    li.dataset.index = i;
    li.draggable = true;
    const img = document.createElement("div");
    img.className = "thumb-img";
    img.style.height = `${104 * vh / vw}px`;
    const dpi = Math.max(8, Math.round(104 * 72 / vw * (window.devicePixelRatio || 1)));
    img.style.backgroundImage = `url("${url("/api/render", { page: i, dpi, v: p.key })}")`;
    const row = document.createElement("div");
    row.className = "thumb-row";
    const num = document.createElement("span");
    num.textContent = String(i + 1);
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = ui.selectedPages.has(i);
    check.setAttribute("aria-label", `Select page ${i + 1}`);
    check.addEventListener("click", (e) => e.stopPropagation());
    check.addEventListener("change", () => {
      check.checked ? ui.selectedPages.add(i) : ui.selectedPages.delete(i);
      renderSelectionBar();
    });
    row.append(num, check);
    const tools = document.createElement("div");
    tools.className = "thumb-tools";
    const mk = (icon, title, fn) => {
      const b = document.createElement("button");
      b.innerHTML = `<svg><use href="#${icon}"/></svg>`;
      b.title = title;
      b.setAttribute("aria-label", title);
      if (icon === "i-trash") b.className = "danger";
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    tools.append(
      mk("i-rotl", "Rotate left", () => op({ op: "rotate", page: i, delta: -90 }).catch(() => {})),
      mk("i-rotr", "Rotate right", () => op({ op: "rotate", page: i, delta: 90 }).catch(() => {})),
      mk("i-trash", "Delete page", () => deletePages([i])),
    );
    li.append(img, row, tools);
    li.addEventListener("click", () => scrollToPage(i));
    li.addEventListener("dragstart", (e) => { dragFrom = i; li.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    li.addEventListener("dragend", () => li.classList.remove("dragging"));
    li.addEventListener("dragover", (e) => {
      if (dragFrom === null) return;
      e.preventDefault();
      const after = e.offsetY > li.clientHeight / 2;
      document.querySelectorAll(".drop-before, .drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));
      li.classList.add(after ? "drop-after" : "drop-before");
    });
    li.addEventListener("drop", (e) => {
      e.preventDefault();
      const after = li.classList.contains("drop-after");
      document.querySelectorAll(".drop-before, .drop-after").forEach((n) => n.classList.remove("drop-before", "drop-after"));
      let to = after ? i + 1 : i;
      const from = dragFrom;
      dragFrom = null;
      if (from === null || to === from || to === from + 1) return;
      ui.selectedPages.clear();
      op({ op: "move_page", from, to: to >= pages.length ? -1 : to }).catch(() => {});
    });
    list.append(li);
  });
  renderSelectionBar();
  markCurrent();
}

function renderSelectionBar() {
  const n = ui.selectedPages.size;
  $("#selection-bar").hidden = n === 0;
  $("#selection-count").textContent = n === 1 ? "1 page selected" : `${n} pages selected`;
}

function scrollToPage(i) {
  ui.current = i;
  document.querySelector(`.page[data-index="${i}"]`)?.scrollIntoView({ block: "start" });
  markCurrent();
}

async function deletePages(indices) {
  const pages = ui.state.pages.length;
  if (indices.length >= pages) return toast("A PDF needs at least one page. Keep at least one.", true);
  ui.selectedPages.clear();
  await op({ op: "delete_pages", pages: indices }).catch(() => {});
}

function renderRedactBar() {
  const n = ui.state.pending_redactions;
  $("#redact-bar").hidden = n === 0;
  $("#redact-count").textContent = n === 1 ? "1 area marked for redaction" : `${n} areas marked for redaction`;
}

// -- dialogs and messages -------------------------------------------------------------------

let toastTimer;
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 3500);
}

function confirmDialog(title, text, ok, danger = false) {
  return new Promise((resolve) => {
    const d = $("#dialog");
    $("#dialog-title").textContent = title;
    $("#dialog-text").textContent = text;
    $("#dialog-ok").textContent = ok;
    $("#dialog-ok").className = danger ? "danger" : "primary";
    d.returnValue = "";
    d.addEventListener("close", () => resolve(d.returnValue === "ok"), { once: true });
    d.showModal();
  });
}

// -- wiring -------------------------------------------------------------------------------

function wire() {
  document.querySelectorAll(".tool").forEach((b) => b.addEventListener("click", () => setTool(b.dataset.tool)));
  $("#undo").addEventListener("click", () => request("/api/undo").then(setState).catch(() => {}));
  $("#redo").addEventListener("click", () => request("/api/redo").then(setState).catch(() => {}));
  $("#zoom-in").addEventListener("click", () => zoomBy(1));
  $("#zoom-out").addEventListener("click", () => zoomBy(-1));
  $("#zoom-level").addEventListener("click", () => { ui.zoom = "fit"; computeScale(); renderPages(); });
  $("#color").addEventListener("click", () => {
    const box = $("#swatches");
    box.hidden = !box.hidden;
    $("#color").setAttribute("aria-expanded", String(!box.hidden));
  });
  $("#download").addEventListener("click", () => { location.href = url("/api/download"); });
  $("#save").addEventListener("click", save);
  $("#add-blank").addEventListener("click", () => op({ op: "new_page", after: ui.current }).then(() => scrollToPage(ui.current + 1)).catch(() => {}));
  $("#insert-pdf").addEventListener("click", async () => {
    const file = await pickFile("#file-pdf");
    if (!file) return;
    const before = ui.state.pages.length;
    await op({ op: "insert_pdf", blob: await upload(file), after: ui.current }).catch(() => {});
    toast(`Inserted ${ui.state.pages.length - before} pages after page ${ui.current + 1}`);
  });
  $("#extract").addEventListener("click", () => {
    const pages = [...ui.selectedPages].sort((a, b) => a - b).join(",");
    location.href = url("/api/download", { pages });
  });
  $("#delete-selected").addEventListener("click", () => deletePages([...ui.selectedPages]));
  $("#redact-apply").addEventListener("click", async () => {
    const yes = await confirmDialog("Apply redactions?",
      "Everything under the marked areas is removed from the document: text, parts of images, notes and form fields.\n\nYou can undo this until you close the editor. The saved file won't contain the removed content.",
      "Apply redactions", true);
    if (yes) op({ op: "redact_apply" }).then(() => toast("Redactions applied. Save to keep them.")).catch(() => {});
  });
  window.addEventListener("resize", () => { if (ui.zoom === "fit" && ui.state) { computeScale(); renderPages(); } });
  window.addEventListener("beforeunload", (e) => { if (ui.state?.dirty) { e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("keydown", (e) => {
    const typing = e.target.closest?.("input, textarea, select");
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.key.toLowerCase() === "s") { e.preventDefault(); save(); return; }
    if (typing) return;
    if (mod && (e.key === "=" || e.key === "+" || e.key === "-")) {
      e.preventDefault();
      zoomBy(e.key === "-" ? -1 : 1);
      return;
    }
    if (mod && e.key.toLowerCase() === "z") {
      e.preventDefault();
      (e.shiftKey ? $("#redo") : $("#undo")).click();
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && ui.selectedAnnot) { e.preventDefault(); deleteSelectedAnnot(); return; }
    if (mod || e.altKey) return;
    const keys = { v: "select", h: "highlight", u: "underline", k: "strikeout", n: "note", b: "freetext", d: "ink", r: "rect",
      e: "ellipse", l: "line", t: "select", x: "select", i: "image" };
    const key = e.key.toLowerCase();
    if (key === "r" && e.shiftKey) setTool("redact");
    else if (keys[key] && !e.shiftKey) setTool(keys[key]);
    else if (e.key === "Escape") setTool("select");
  });
}

async function save() {
  try {
    const res = await request("/api/save", { body: {} });
    setState(res);
    toast(`Saved as ${res.path}`);
  } catch { /* toast shown */ }
}

async function start() {
  wire();
  setTool("select");
  if (!TOKEN) {
    toast("This address is missing its access key. Open the link that clearleaf edit printed.", true);
    return;
  }
  try {
    setState(await request("/api/state", { method: "GET" }));
  } catch { /* toast shown */ }
}

start();
