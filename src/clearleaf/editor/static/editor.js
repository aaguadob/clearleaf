"use strict";

// clearleaf editor. The server holds the document; every edit is one
// operation posted to /api/op, and the reply is the new document state.

const TOKEN = new URLSearchParams(location.search).get("t") || "";
const $ = (sel) => document.querySelector(sel);
const PALETTE = ["#f2d10a", "#2e7d5a", "#1f5fd1", "#c0352b", "#1c2521", "#8a4fd1"];
const TOOL_COLOR = { highlight: 0, underline: 2, strikeout: 3, ink: 2, rect: 3, ellipse: 3, line: 3, freetext: 4, text: 4, note: 0 };
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
  el.textContent = ui.busy ? "Working…" : ui.state.dirty ? "Edited, not saved" : "All changes saved";
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
  const open = [...container.querySelectorAll(".inline-editor, .inline-hint")].map((n) => ({
    node: n, page: n.closest(".page")?.dataset.index, focused: document.activeElement === n }));
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
    if (o.focused) o.node.focus();
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
    if (final) img.dataset.final = src;
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

function buildOverlays(el, p, i) {
  const layer = el.querySelector(".layer");
  for (const a of p.annots) {
    const hit = document.createElement("div");
    hit.className = "annot-hit" + (a.type === "Redact" ? " redact" : "");
    place(hit, rectToView(p, a.rect));
    hit.title = a.type === "Redact" ? "Marked for redaction" : a.content || a.type;
    hit.addEventListener("pointerdown", (e) => {
      if (ui.tool !== "select") return;
      e.stopPropagation();
      selectAnnot(hit, i, a);
    });
    layer.append(hit);
  }
  for (const f of p.fields) layer.append(fieldInput(p, i, f));
  if (ui.tool === "edittext") addTextLines(el, p, i);
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
  tip.textContent = a.content ? `${label}: ${a.content}` : label;
  const del = document.createElement("button");
  del.textContent = a.type === "Redact" ? "Remove mark" : "Delete";
  del.addEventListener("pointerdown", (e) => e.stopPropagation());
  del.addEventListener("click", () => deleteSelectedAnnot());
  tip.append(document.createElement("br"), del);
  hit.append(tip);
}

function labelFor(type) {
  return { Highlight: "Highlight", Underline: "Underline", StrikeOut: "Strikeout", Squiggly: "Squiggly underline", Text: "Note",
    FreeText: "Text box", Ink: "Drawing", Square: "Rectangle", Circle: "Ellipse", Line: "Line", Link: "Link", Stamp: "Stamp" }[type] || type;
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

async function addTextLines(el, p, i) {
  const lines = await linesFor(i);
  const layer = el.querySelector(".layer");
  for (const line of lines) {
    const box = document.createElement("div");
    box.className = "textline";
    box.title = "Click to edit this line";
    place(box, rectToView(p, line.bbox));
    box.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
      e.preventDefault();
      editLine(el, p, i, line);
    });
    layer.append(box);
  }
}

function editLine(el, p, i, line) {
  const r = rectToView(p, line.bbox);
  inlineEditor(el, {
    rect: [r[0] - 2, r[1] - 2, Math.max(r[2] + 40, r[0] + 160), r[3] + 2],
    value: line.text,
    fontSize: line.size,
    multiline: false,
    hint: "Enter to replace the line · Esc to cancel · the new text is set in Helvetica",
    commit: (text) => {
      if (text === line.text) return;
      const b = line.bbox;
      op({ op: "replace_text", page: i, rect: [b[0] - 0.5, b[1] - 0.5, b[2] + 0.5, b[3] + 0.5], text }).catch(() => {});
    },
  });
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
  if (e.target.closest(".field, .inline-editor, .annot-tip")) return;
  if (ui.current !== i) { ui.current = i; markCurrent(); }
  const tool = ui.tool;
  if (tool === "select") {
    document.querySelectorAll(".annot-hit.selected").forEach((n) => { n.classList.remove("selected"); n.replaceChildren(); });
    ui.selectedAnnot = null;
    return;
  }
  if (tool === "edittext") return;
  e.preventDefault();
  const start = pointIn(e, el, p);
  const layer = el.querySelector(".layer");

  if (tool === "note" || tool === "text") {
    const [vx, vy] = start.view;
    const size = 12;
    inlineEditor(el, {
      rect: tool === "note" ? [vx, vy, vx + 200, vy + 90] : [vx, vy - size * 0.2, vx + 260, vy + size * 1.2],
      value: "",
      fontSize: size,
      multiline: tool === "note",
      hint: tool === "note" ? "Type the note · ⌘Enter to add · Esc to cancel" : "Type · Enter to add · Esc to cancel",
      commit: (text) => {
        if (!text.trim()) return;
        if (tool === "note") op({ op: "note", page: i, point: start.page, text }).catch(() => {});
        else {
          const [x, y] = toPage(p, vx, vy + size * 0.8);
          op({ op: "text", page: i, point: [x, y], text, fontsize: size, color: hex(color()) }).catch(() => {});
        }
      },
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
  ui.tool = tool;
  document.body.dataset.tool = tool;
  document.querySelectorAll(".tool").forEach((b) => b.classList.toggle("active", b.dataset.tool === tool));
  document.documentElement.style.setProperty("--tool", tool === "redact" ? "#c0352b" : tool in TOOL_COLOR ? color() : "#2e7d5a");
  renderSwatches();
  if (ui.state) renderPages();
}

function renderSwatches() {
  const box = $("#swatches");
  box.replaceChildren();
  if (!(ui.tool in TOOL_COLOR)) return;
  PALETTE.forEach((c, k) => {
    const b = document.createElement("button");
    b.className = "swatch" + (ui.colors[ui.tool] === k ? " active" : "");
    b.style.background = c;
    b.title = "Color";
    b.setAttribute("aria-label", `Color ${k + 1}`);
    b.addEventListener("click", () => {
      ui.colors[ui.tool] = k;
      document.documentElement.style.setProperty("--tool", c);
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
  $("#pagecount").textContent = pages.length === 1 ? "1 page" : `${pages.length} pages`;
  let dragFrom = null;
  pages.forEach((p, i) => {
    const [vw, vh] = viewSize(p);
    const li = document.createElement("li");
    li.className = "thumb";
    li.dataset.index = i;
    li.draggable = true;
    const img = document.createElement("div");
    img.className = "thumb-img";
    img.style.height = `${120 * vh / vw}px`;
    const dpi = Math.max(8, Math.round(120 * 72 / vw * (window.devicePixelRatio || 1)));
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
    const mk = (label, title, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.title = title;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };
    tools.append(
      mk("↺", "Rotate left", () => op({ op: "rotate", page: i, delta: -90 }).catch(() => {})),
      mk("↻", "Rotate right", () => op({ op: "rotate", page: i, delta: 90 }).catch(() => {})),
      mk("✕", "Delete page", () => deletePages([i])),
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
  $("#zoom").addEventListener("change", (e) => { ui.zoom = e.target.value; computeScale(); renderPages(); });
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
    if (mod && e.key.toLowerCase() === "z") {
      e.preventDefault();
      (e.shiftKey ? $("#redo") : $("#undo")).click();
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && ui.selectedAnnot) { e.preventDefault(); deleteSelectedAnnot(); return; }
    if (mod || e.altKey) return;
    const keys = { v: "select", h: "highlight", u: "underline", k: "strikeout", n: "note", b: "freetext", d: "ink", r: "rect",
      e: "ellipse", l: "line", t: "text", x: "edittext", i: "image" };
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
