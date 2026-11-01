"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v; else n.setAttribute(k, v);
    }
    for (const k of kids) n.append(k instanceof Node ? k : document.createTextNode(String(k)));
    return n;
  };
  const SVG = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs = {}) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };

  // Per-browser pseudonymous session (convenience only; the server hashes it).
  let session = null;
  try { session = localStorage.getItem("stetho.session"); } catch (_) {}
  if (!session) {
    session = Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
    try { localStorage.setItem("stetho.session", session); } catch (_) {}
  }

  const DECISION = {
    rerecord: ["b-info", "↻", "Please record again", "The recording was not clear enough to judge."],
    baseline_saved: ["b-ok", "✓", "Saved as a healthy reference", "Future checks will be compared with this."],
    no_change: ["b-ok", "✓", "No change from its own normal", "Keep routine checks going."],
    watch: ["b-watch", "!", "Small change: record again soon", "Not clear yet. Re-check from the same spot."],
    technician: ["b-act", "⚠", "Ask a technician to inspect", "A clear change or strong pattern was found."],
    no_reference: ["b-info", "?", "Can't judge yet: no healthy reference", "Save healthy recordings first."],
    repair_check: ["b-info", "⇄", "Repair check", ""],
    ambient_saved: ["b-ok", "♪", "Background noise saved", "Now switch the machine on and record from the same spot."],
  };
  const REPAIR = {
    back_to_baseline: ["b-ok", "Back close to its healthy reference"],
    improved: ["b-watch", "Closer to healthy, not fully back"],
    no_clear_change: ["b-act", "No clear change after repair"],
    worse: ["b-act", "Further from healthy than before repair"],
    not_available: ["b-info", "Needs a healthy reference and a before-repair recording"],
  };
  const FEATURE_LABEL = {
    "a.hf_energy_ratio_db": "High-pitch hiss share", "a.hf_kurtosis": "Impulsiveness (clicks/knocks)",
    "a.hf_crest_db": "Peakiness of high-pitch sound", "a.envelope_prominence_db": "Regularity of knocks",
    "a.harmonic_1x_db": "Hum at shaft speed (1×)", "a.harmonic_2x_db": "Hum at 2× shaft speed",
    "v.peak_amp_db": "Visible shaking (peak)", "v.band_rms_db": "Visible shaking (overall)",
  };
  const label = (f) => FEATURE_LABEL[f] || (f.startsWith("a.band_") ? `Sound share ${f.slice(7).replace("_", "–")} Hz` : f);
  const QLABEL = {
    duration: "Length", brightness: "Brightness", sharpness: "Sharpness", frame_rate: "Frame rate",
    frame_timing: "Frame timing", compression: "Video compression cycle", camera_shake: "Camera shake", camera_rotation: "Camera rotation",
    motion_noise_floor: "Tracking noise floor", motion_tracking: "Motion tracking", audio: "Sound",
    audio_level: "Sound level", audio_clipping: "Sound clipping", background_noise: "Background noise", video: "Video",
  };

  async function health() {
    try {
      const h = await (await fetch("/healthz")).json();
      $("status").textContent = `Server v${h.version} · segmentation: ${h.segmenter}${h.segmenter_note ? " (" + h.segmenter_note + ")" : ""} · voice: ${h.tts ? "on" : "off"} · language model: ${h.llm ? "on (guarded)" : "off (templates)"} · WhatsApp: ${h.whatsapp ? "on" : "off"}`;
    } catch (_) { $("status").textContent = "Server not reachable."; }
  }

  function busy(on, text) {
    $("busy").hidden = !on; $("busyText").textContent = text || "Analysing…";
    document.querySelectorAll("button, #form input, #form select").forEach((b) => { b.disabled = on; });
  }
  function showError(msg) { const e = $("error"); e.textContent = msg; e.hidden = !msg; }

  const ERRORS = {
    file_too_large: "That video is too large. Record 10–15 seconds and try again.",
    unsupported_container: "That file type isn't supported. Please use a normal phone video (MP4 or MOV).",
    unreadable_media: "We couldn't read that file. Please try another video.",
    too_few_frames: "The video is too short.", analysis_timeout: "The check took too long. Try a shorter video.",
    empty_upload: "The upload was empty. Please choose the video again.",
  };

  async function post(url, fd, text) {
    showError(""); busy(true, text);
    const t0 = performance.now();
    try {
      const r = await fetch(url, { method: "POST", body: fd });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(ERRORS[body.detail] || body.detail || `Request failed (${r.status})`);
      render(body, (performance.now() - t0) / 1000);
      machines();
    } catch (e) { showError(e.message || "Upload failed. Check your connection and try again."); }
    finally { busy(false); }
  }

  $("form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = $("video").files[0];
    if (!f) return showError("Choose a video first.");
    const fd = new FormData();
    fd.append("video", f); fd.append("session", session);
    fd.append("machine", $("machine").value || "pump"); fd.append("lang", $("lang").value);
    fd.append("mode", document.querySelector("input[name=mode]:checked").value);
    if ($("rpm").value) fd.append("rpm", $("rpm").value);
    post("/api/analyze", fd, "Uploading and analysing… usually well under a minute.");
  });

  document.querySelectorAll("[data-demo]").forEach((b) => b.addEventListener("click", () => {
    const fd = new FormData();
    fd.append("session", session); fd.append("lang", $("lang").value);
    fd.append("mode", $("demoAfter").checked ? "after_repair" : "check");
    post(`/api/demo/${b.dataset.demo}`, fd, "Generating a simulated recording and analysing it…");
  }));

  function render(r, wallS) {
    const a = r.assessment, x = r.explanation;
    const box = $("result"); box.hidden = false;
    let [cls, icon, title, sub] = DECISION[a.decision] || ["b-info", "•", a.decision, ""];
    if (a.decision === "repair_check") {
      const rp = REPAIR[(a.repair || {}).status || "not_available"]; cls = rp[0]; sub = rp[1];
    }
    const spotStatus = (a.spot || {}).status;
    if (spotStatus === "moved") sub += " Note: the phone was not at the usual spot, so movement was not compared.";
    if (spotStatus === "different_view") sub += " Note: this view doesn't match the reference. Is it the same machine?";
    if ((a.trend || {}).status === "rising") sub += " The trend across checks is rising.";
        const banner = $("banner"); banner.className = `banner ${cls}`; banner.replaceChildren(
      el("span", { class: "icon", "aria-hidden": "true" }, icon),
      el("div", {}, title, r.simulated_input ? el("span", { class: "sim" }, "SIMULATED INPUT") : "", el("small", {}, sub)));

    const voice = $("voice");
    if (r.artifacts.voice) { voice.src = r.artifacts.voice; voice.hidden = false; } else { voice.hidden = true; }
    $("message").replaceChildren(...x.chat_text.split("\n").map((l) => el("p", { lang: x.language }, l)));

    const fs = a.findings.length ? a.findings.map((f) => el("div", { class: "finding" },
      el("b", {}, f.id.replaceAll("_", " ")), el("span", { class: "pill" }, f.strength),
      el("span", { class: "pill" }, f.basis === "own_baseline" ? "vs own baseline" : "absolute rule (uncalibrated)"),
      el("div", { class: "ev" }, f.evidence.map((e) => `${label(e.label)}: ${e.value} ${e.unit}` + (e.baseline !== undefined ? ` (baseline ${e.baseline}, z ${e.z})` : "")).join(" · ")),
      f.hypotheses && f.hypotheses.length ? el("div", { class: "ev" }, "Possible causes to check: " + f.hypotheses.map((h) => h.replaceAll("_", " ")).join(", ")) : ""))
      : [el("p", { class: "muted" }, "No patterns flagged.")];
    $("findings").replaceChildren(...fs);

    zChart(a.comparison);
    contextList(a);
    historyChart(r.history || [], a.trend || {});
    spectrum(r.motion_spectrum, r.motion);
    const xm = a.cross_modal || {};
    $("xmodal").textContent = xm.status && xm.status !== "not_available"
      ? `Cross-check: video motion peak ${xm.video_peak_hz} Hz; audio shaft speed predicts ${xm.expected_video_hz} Hz in this video${xm.aliased ? " (aliased: the camera frame rate is too low to see the true frequency)" : ""} → ${xm.status === "agree" ? "they agree" : xm.status === "agree_codec_ambiguous" ? `consistent, but only after allowing for video compression (codec cycle ${xm.codec_cycle_hz} Hz), so weaker support` : "they disagree"}.`
      : "Cross-check between sound and video not available for this recording.";

    $("quality").replaceChildren(...r.quality.checks.map((c) =>
      el("li", {}, el("span", { class: `st st-${c.status}` }, c.status), ` ${QLABEL[c.id] || c.id}: ${c.value ?? ""} ${c.unit || ""}`)));

    const mag = $("mag");
    if (r.artifacts.magnified) { mag.src = r.artifacts.magnified; $("magWrap").hidden = false; } else { $("magWrap").hidden = true; }
    $("tech").textContent = x.technician_summary;
    const st = r.timing.stages_ms;
    $("timing").textContent = `Server analysis ${(r.timing.total_ms / 1000).toFixed(1)} s (target ${(r.timing.target_ms / 1000).toFixed(0)} s; ${r.timing.within_target ? "met" : "missed"}). Round trip in your browser ${wallS.toFixed(1)} s. Stages: ` +
      Object.entries(st).map(([k, v]) => `${k} ${(v / 1000).toFixed(1)}s`).join(", ") + `. Text: ${x.source}.`;
    $("disclaimer").textContent = r.disclaimer;
    $("json").href = URL.createObjectURL(new Blob([JSON.stringify(r, null, 1)], { type: "application/json" }));
    box.focus();
  }

  function zChart(cmp) {
    const host = $("zchart");
    if (!cmp || !cmp.z || !Object.keys(cmp.z).length) {
      host.replaceChildren(el("p", { class: "muted small" }, "No healthy reference yet for this machine, so there is nothing to compare."));
      return;
    }
    const rows = Object.entries(cmp.z); const W = 640, rowH = 22, left = 230, H = rows.length * rowH + 30;
    const max = Math.max(6, ...rows.map(([, z]) => Math.min(10, Math.abs(z))));
    const sx = (z) => left + ((Math.max(-max, Math.min(max, z)) + max) / (2 * max)) * (W - left - 44);
    const s = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Change versus healthy reference, per measurement" });
    for (const t of [-3, 3]) s.append(svgEl("line", { x1: sx(t), x2: sx(t), y1: 0, y2: H - 20, stroke: "var(--muted)", "stroke-dasharray": "4 3" }));
    s.append(svgEl("line", { x1: sx(0), x2: sx(0), y1: 0, y2: H - 20, stroke: "var(--line)" }));
    rows.forEach(([f, z], i) => {
      const y = i * rowH + 4, notable = Math.abs(z) >= 3;
      const t = svgEl("text", { x: left - 8, y: y + 13, "text-anchor": "end" }); t.textContent = label(f); s.append(t);
      s.append(svgEl("rect", { x: Math.min(sx(0), sx(z)), y, width: Math.max(2, Math.abs(sx(z) - sx(0))), height: rowH - 8, rx: 3,
        fill: notable ? "var(--act)" : "var(--series-1)" }));
      const v = svgEl("text", { x: z >= 0 ? sx(z) + 4 : sx(z) - 4, y: y + 13, "text-anchor": z >= 0 ? "start" : "end" }); v.textContent = (Math.abs(z) < 0.05 ? 0 : z).toFixed(1); s.append(v);
    });
    const ax = svgEl("text", { x: sx(0), y: H - 4, "text-anchor": "middle" }); ax.textContent = `0 = same as healthy reference · ${cmp.n_baselines} reference recording(s)`; s.append(ax);
    host.replaceChildren(s);
  }

  function contextList(a) {
    const sp = a.spot || {}, am = a.ambient || {}, tr = a.trend || {};
    const SPOT = { same_spot: ["ok", "Same spot as the healthy reference"],
      moved: ["warn", "Phone was not at the usual spot. Movement was not compared"],
      different_view: ["warn", "View doesn't match the reference. Is this the same machine?"],
      not_available: ["info", "No reference view yet"] };
    const s = SPOT[sp.status] || SPOT.not_available;
    const items = [el("li", {}, el("span", { class: `st st-${s[0]}` }, s[0]), " " + s[1] +
      (sp.scale ? ` (scale ${sp.scale}, shift ${Math.round(sp.shift_frac * 100)}% of width)` : ""))];
    if (am.status === "applied") items.push(el("li", {}, el("span", { class: `st st-${am.excluded.length ? "warn" : "ok"}` }, am.excluded.length ? "warn" : "ok"),
      ` Background reference (${am.age_min} min old): machine ${am.overall_margin_db} dB above background` +
      (am.excluded.length ? `; ignored ${am.excluded.length} sound measurement(s) drowned by background` : "")));
    else if (am.status === "mismatch") items.push(el("li", {}, el("span", { class: "st st-warn" }, "warn"), " Saved background reference no longer matches. Not used"));
    else items.push(el("li", {}, el("span", { class: "st st-info" }, "info"), " No background reference (optional: save one with the machine off)"));
    items.push(el("li", {}, el("span", { class: `st st-${tr.status === "rising" ? "warn" : "ok"}` }, tr.status === "rising" ? "warn" : tr.status === "steady" ? "ok" : "info"),
      tr.status === "rising" ? ` Trend: rising over ${tr.n} checks` : tr.status === "steady" ? ` Trend: steady over ${tr.n} checks` : " Trend: needs 4 or more checks"));
    $("context").replaceChildren(...items);
  }

  function historyChart(hist, trend) {
    const host = $("history");
    const pts = hist.filter((h) => h.distance !== null && h.distance !== undefined);
    if (pts.length < 2) { host.replaceChildren(el("p", { class: "muted small" }, "Not enough compared checks yet.")); return; }
    const W = 640, H = 170, l = 36, b = 22, top = 10;
    const ymax = Math.max(4, ...pts.map((p) => p.distance));
    const sx = (i) => l + (i / Math.max(1, pts.length - 1)) * (W - l - 12), sy = (v) => H - b - (v / ymax) * (H - b - top);
    const s = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Distance from healthy reference over checks" });
    s.append(svgEl("line", { x1: l, x2: W - 12, y1: sy(3), y2: sy(3), stroke: "var(--muted)", "stroke-dasharray": "4 3" }));
    const t3 = svgEl("text", { x: W - 12, y: sy(3) - 4, "text-anchor": "end" }); t3.textContent = "clearly different"; s.append(t3);
    s.append(svgEl("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.distance).toFixed(1)}`).join(""),
      fill: "none", stroke: trend.status === "rising" ? "var(--watch)" : "var(--series-1)", "stroke-width": 2 }));
    pts.forEach((p, i) => {
      const c = svgEl("circle", { cx: sx(i), cy: sy(p.distance), r: 4, fill: p.kind === "after_repair" ? "var(--ok)" : p.kind === "baseline" ? "var(--series-2)" : "var(--series-1)" });
      const tt = svgEl("title"); tt.textContent = `${p.kind}: ${p.decision} (distance ${p.distance})`; c.append(tt); s.append(c);
    });
    const yl = svgEl("text", { x: 2, y: sy(0) }); yl.textContent = "0"; s.append(yl);
    const xl = svgEl("text", { x: l, y: H - 4 }); xl.textContent = "older"; s.append(xl);
    const xr = svgEl("text", { x: W - 12, y: H - 4, "text-anchor": "end" }); xr.textContent = "latest"; s.append(xr);
    host.replaceChildren(s);
  }

  function spectrum(sp, mo) {
    const host = $("spectrum");
    if (!sp) { host.replaceChildren(el("p", { class: "muted small" }, mo && mo.reason ? `Motion not measured: ${mo.reason.replaceAll("_", " ")}.` : "Motion not measured.")); return; }
    const W = 640, H = 230, l = 44, b = 24, top = 40;
    const f = sp.freqs, lo = 2, hi = f[f.length - 1] * 0.9;
    const idx = f.map((v, i) => i).filter((i) => f[i] >= lo && f[i] <= hi);
    const db = (p) => 10 * Math.log10(p + 1e-12);
    const all = idx.flatMap((i) => [db(sp.roi[i]), db(sp.ref[i])]);
    const ymin = Math.min(...all), ymax = Math.max(...all);
    const sx = (v) => l + ((v - lo) / (hi - lo)) * (W - l - 8), sy = (v) => H - b - ((v - ymin) / (ymax - ymin || 1)) * (H - b - top);
    const s = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Motion spectrum of machine versus static background" });
    const path = (arr, color, w) => s.append(svgEl("path", { d: idx.map((i, k) => `${k ? "L" : "M"}${sx(f[i]).toFixed(1)},${sy(db(arr[i])).toFixed(1)}`).join(""), fill: "none", stroke: color, "stroke-width": w }));
    path(sp.ref, "var(--series-2)", 1.5); path(sp.roi, "var(--series-1)", 2);
    for (let t = 0; t <= 4; t++) { const v = lo + (t * (hi - lo)) / 4; const tx = svgEl("text", { x: sx(v), y: H - 6, "text-anchor": t === 0 ? "start" : t === 4 ? "end" : "middle" }); tx.textContent = `${v.toFixed(0)} Hz`; s.append(tx); }
    const yl = svgEl("text", { x: 4, y: 12 }); yl.textContent = "px²/Hz (dB)"; s.append(yl);
    const lg1 = svgEl("text", { x: W - 8, y: 14, "text-anchor": "end", fill: "var(--series-1)" }); lg1.textContent = "machine (shake removed)"; s.append(lg1);
    const lg2 = svgEl("text", { x: W - 8, y: 28, "text-anchor": "end" }); lg2.textContent = "still background = noise floor"; s.append(lg2);
    if (mo && mo.peak_hz) { const px = sx(mo.peak_hz); s.append(svgEl("line", { x1: px, x2: px, y1: top, y2: H - b, stroke: "var(--muted)", "stroke-dasharray": "2 3" })); }
    host.replaceChildren(s);
  }

  async function machines() {
    try {
      const ms = await (await fetch(`/api/machines?session=${session}`)).json();
      $("machines").replaceChildren(...(ms.length ? ms.map((m) => el("li", {}, `${m.name}: ${m.baselines} healthy reference(s), ${m.recordings} recording(s) total`))
        : [el("li", { class: "muted" }, "No machines yet.")]));
    } catch (_) {}
  }
  $("delete").addEventListener("click", async () => {
    if (!confirm("Delete all machines, references and results stored for this browser?")) return;
    const r = await (await fetch(`/api/session/${session}`, { method: "DELETE" })).json();
    $("result").hidden = true; machines();
    showError(""); $("busy").hidden = false; $("busyText").textContent = `Deleted ${r.deleted_recordings} recording(s).`;
    setTimeout(() => { $("busy").hidden = true; }, 2500);
  });
  $("copyTech").addEventListener("click", () => navigator.clipboard && navigator.clipboard.writeText($("tech").textContent));
  health(); machines();
})();
