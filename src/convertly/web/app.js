"use strict";
(() => {
  const TOKEN = document.querySelector('meta[name="convertly-token"]').content;
  const MAX_UPLOAD = Number(document.querySelector('meta[name="convertly-max-upload"]').content) || Infinity;
  const $ = (id) => document.getElementById(id);
  const drop = $("drop"), picker = $("picker"), stage = $("stage"), ring = $("ring"), chips = $("chips");
  const results = $("results");
  const PREFERRED = ["pdf", "png", "jpg", "webp", "docx", "md", "html", "txt", "xlsx", "csv", "json", "yaml",
    "mp4", "mp3", "webm", "gif", "avif", "tiff", "m4a", "flac", "zip"];
  const MAX_SLICES = 10, PARALLEL = 3;
  const LABELS = { pages: "split pages", dir: "extract" };
  const label = (t) => LABELS[t] || t;
  let files = [];

  async function api(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "X-Convertly-Token": TOKEN,
      "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
    return r.json();
  }

  function rank(targets, sources) {
    const same = new Set(sources);
    return [...targets].sort((a, b) => {
      const ia = PREFERRED.indexOf(a), ib = PREFERRED.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    }).filter((t) => !same.has(t) || sources.length > 1);
  }

  function el(tag, attrs = {}, text) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  const svg = (tag, attrs = {}) => {
    const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  };

  function drawRing(targets) {
    ring.replaceChildren();
    const n = targets.length;
    if (!n) return;
    const R = 155, r = 66, gap = n > 1 ? 0.02 : 0;
    targets.forEach((t, i) => {
      const a0 = (i / n) * 2 * Math.PI - Math.PI / 2 + gap, a1 = ((i + 1) / n) * 2 * Math.PI - Math.PI / 2 - gap;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const p = (rad, a) => `${(rad * Math.cos(a)).toFixed(2)} ${(rad * Math.sin(a)).toFixed(2)}`;
      const d = n === 1
        ? `M ${-R} 0 A ${R} ${R} 0 1 1 ${R} 0 A ${R} ${R} 0 1 1 ${-R} 0 M ${-r} 0 A ${r} ${r} 0 1 0 ${r} 0 A ${r} ${r} 0 1 0 ${-r} 0 Z`
        : `M ${p(R, a0)} A ${R} ${R} 0 ${large} 1 ${p(R, a1)} L ${p(r, a1)} A ${r} ${r} 0 ${large} 0 ${p(r, a0)} Z`;
      const g = svg("g", { class: "slice", tabindex: "0", role: "menuitem", "aria-label": `Convert to ${label(t)}` });
      g.append(svg("path", { d, "fill-rule": "evenodd" }));
      const mid = (a0 + a1) / 2, tr = (R + r) / 2;
      const text = svg("text", { x: (n === 1 ? 0 : tr * Math.cos(mid)).toFixed(1), y: (n === 1 ? -tr : tr * Math.sin(mid)).toFixed(1) });
      text.textContent = label(t);
      g.append(text);
      g.addEventListener("click", () => start(t));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); start(t); } });
      ring.append(g);
    });
  }

  async function setFiles(list) {
    files = [...list].filter((f) => f.size >= 0);
    if (!files.length) return;
    $("hub-count").textContent = files.length;
    $("hub-label").textContent = files.length === 1 ? files[0].name : "files";
    stage.hidden = false;
    chips.replaceChildren();
    ring.replaceChildren();
    let info;
    try {
      info = await api("/api/targets", { names: files.map((f) => f.name) });
    } catch (e) { $("nothing").hidden = false; $("nothing").textContent = e.message; return; }
    const ranked = rank(info.targets, info.sources);
    $("nothing").hidden = ranked.length > 0;
    if (info.unknown.length) {
      $("nothing").hidden = false;
      $("nothing").textContent = `Unknown file type: ${info.unknown.slice(0, 3).join(", ")}`;
    }
    drawRing(ranked.slice(0, MAX_SLICES));
    const catOf = {};
    for (const [c, list2] of Object.entries(info.byCategory)) for (const t of list2) catOf[t] = c;
    for (const t of ranked) {
      const b = el("button", { class: "chip", type: "button", role: "listitem" }, label(t));
      if (catOf[t] && catOf[t] !== "special") b.append(el("span", { class: "cat" }, catOf[t]));
      b.addEventListener("click", () => start(t));
      chips.append(b);
    }
  }

  function options() {
    const q = new URLSearchParams();
    const add = (k, id) => { const v = $(id).value.trim(); if (v) q.set(k, v); };
    add("quality", "o-quality"); add("width", "o-width"); add("height", "o-height");
    add("pages", "o-pages"); add("dpi", "o-dpi");
    if ($("o-gray").checked) q.set("grayscale", "1");
    return q;
  }

  function row(file, to) {
    const li = el("li");
    const name = el("div", { class: "name" }, `${file.name} → ${label(to).toUpperCase()}`);
    const bar = el("div", { class: "bar" }); bar.append(el("i"));
    const meta = el("div", { class: "meta" }, "waiting…");
    const act = el("div", { class: "act" });
    li.append(name, act, bar, meta);
    results.prepend(li);
    $("results-title").hidden = false;
    return { li, bar, meta, act };
  }

  function convertOne(file, to, ui) {
    return new Promise((resolve) => {
      if (file.size > MAX_UPLOAD) {
        ui.meta.replaceChildren(el("span", { class: "err" }, `larger than ${Math.round(MAX_UPLOAD / 1048576)} MB limit`));
        return resolve();
      }
      const q = options(); q.set("to", to); q.set("name", file.name);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/convert?" + q.toString());
      xhr.setRequestHeader("X-Convertly-Token", TOKEN);
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.responseType = "blob";
      const t0 = performance.now();
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) { ui.bar.firstChild.style.width = `${(e.loaded / e.total) * 100}%`; ui.meta.textContent = "sending…"; }
      };
      xhr.upload.onload = () => { ui.bar.classList.add("busy"); ui.bar.firstChild.style.width = ""; ui.meta.textContent = "converting…"; };
      xhr.onload = async () => {
        ui.bar.classList.remove("busy");
        if (xhr.status === 200) {
          const fname = decodeURIComponent(xhr.getResponseHeader("X-Convertly-Filename") || `converted.${to}`);
          const route = decodeURIComponent(xhr.getResponseHeader("X-Convertly-Route") || "");
          const url = URL.createObjectURL(xhr.response);
          const a = el("a", { class: "dl", href: url, download: fname }, "Download");
          ui.act.replaceChildren(a);
          ui.bar.firstChild.style.width = "100%";
          ui.meta.textContent = `${fname} · ${(xhr.response.size / 1024).toFixed(1)} KB · ${((performance.now() - t0) / 1000).toFixed(1)}s · ${route}`;
          a.click();
        } else {
          let msg = xhr.statusText;
          try { msg = JSON.parse(await xhr.response.text()).error || msg; } catch (_) { /* not JSON */ }
          ui.meta.replaceChildren(el("span", { class: "err" }, msg));
        }
        resolve();
      };
      xhr.onerror = () => { ui.meta.replaceChildren(el("span", { class: "err" }, "connection lost")); resolve(); };
      xhr.send(file);
    });
  }

  async function start(to) {
    const queue = files.map((f) => [f, row(f, to)]);
    const workers = Array.from({ length: Math.min(PARALLEL, queue.length) }, async () => {
      while (queue.length) { const [f, ui] = queue.shift(); await convertOne(f, to, ui); }
    });
    await Promise.all(workers);
  }

  // Drag and drop anywhere on the page.
  let depth = 0;
  window.addEventListener("dragenter", (e) => { e.preventDefault(); depth++; drop.classList.add("over"); });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; drop.classList.remove("over"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; drop.classList.remove("over");
    if (e.dataTransfer && e.dataTransfer.files.length) setFiles(e.dataTransfer.files);
  });
  drop.addEventListener("click", () => picker.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picker.click(); } });
  picker.addEventListener("change", () => setFiles(picker.files));
  $("clear").addEventListener("click", () => { files = []; stage.hidden = true; picker.value = ""; });
  window.addEventListener("paste", (e) => { if (e.clipboardData && e.clipboardData.files.length) setFiles(e.clipboardData.files); });

  fetch("/api/engines", { headers: { "X-Convertly-Token": TOKEN } }).then((r) => r.json()).then((list) => {
    const on = list.filter((e) => e.available).map((e) => e.name);
    $("engines").textContent = on.length ? `Engines: ${on.join(", ")}.` : "No optional engines found.";
  }).catch(() => {});
})();
