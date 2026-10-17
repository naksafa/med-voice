/* MedVoice frontend — vanilla JS, talks to the FastAPI backend under /api */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

const state = {
  health: null,
  lang: pref("lang", "fr"),
  engine: pref("engine", null),
  useAI: pref("useAI", "1") === "1",
  patient: null,
  consultationId: null,
  conditions: [],
  symptoms: [],
  items: [],
  warnings: [],
};

function pref(key, fallback) {
  try { return localStorage.getItem("medvoice." + key) ?? fallback; } catch { return fallback; }
}
function savePref(key, value) {
  try { localStorage.setItem("medvoice." + key, value); } catch { /* storage unavailable */ }
}

// ------------------------------------------------------------------ API
async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {}) } };
  if (init.body && !(init.body instanceof FormData) && typeof init.body !== "string") init.body = JSON.stringify(init.body);
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* not json */ }
    throw new Error(msg);
  }
  if (opts.raw) return res;
  return res.status === 204 ? null : res.json();
}

function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (error ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 3200);
}

async function busy(btn, fn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Working…";
  try { return await fn(); }
  catch (e) { toast(e.message, true); }
  finally { btn.disabled = false; btn.textContent = label; }
}

// ------------------------------------------------------------------ dictation
let activeDictation = null;

class Dictation {
  constructor(root) {
    this.root = root;
    this.textarea = $("textarea", root);
    this.btn = $(".mic", root);
    this.label = $(".mic-label", root);
    this.status = $(".mic-status", root);
    this.recording = false;
    this.btn.addEventListener("click", () => (this.recording ? this.stop() : this.start()));
  }

  async start() {
    if (activeDictation && activeDictation !== this) activeDictation.stop();
    activeDictation = this;
    const engine = state.engine;
    try {
      if (engine === "whisper") await this.startWhisper();
      else this.startBrowser();
      this.recording = true;
      this.btn.classList.add("rec");
      this.label.textContent = "Stop";
      this.status.textContent = "Listening…";
    } catch (e) {
      toast("Microphone: " + e.message, true);
    }
  }

  stop() {
    this.recording = false;
    this.btn.classList.remove("rec");
    this.label.textContent = "Start dictation";
    if (this.recognition) { this.recognition.onend = null; this.recognition.stop(); this.recognition = null; this.status.textContent = ""; }
    if (this.recorder && this.recorder.state !== "inactive") this.recorder.stop();
    if (activeDictation === this) activeDictation = null;
  }

  append(text) {
    text = text.trim();
    if (!text) return;
    const cur = this.textarea.value.trimEnd();
    this.textarea.value = cur ? cur + " " + text : text;
    this.textarea.dispatchEvent(new Event("input"));
  }

  // Web Speech API (Chrome / Edge). Streams words live.
  startBrowser() {
    if (!SpeechRecognition) throw new Error("this browser has no speech recognition — use Chrome/Edge or the Whisper engine");
    const rec = new SpeechRecognition();
    rec.lang = state.lang === "fr" ? "fr-FR" : "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.append(r[0].transcript);
        else interim += r[0].transcript;
      }
      this.status.textContent = interim || "Listening…";
    };
    rec.onerror = (e) => { if (e.error !== "no-speech") toast("Speech: " + e.error, true); };
    rec.onend = () => { if (this.recording) rec.start(); }; // Chrome stops after silence: keep going
    rec.start();
    this.recognition = rec;
  }

  // Record audio, send to the local Whisper model when stopped.
  async startWhisper() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks = [];
    const recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
      const ext = (recorder.mimeType || "").includes("ogg") ? ".ogg" : (recorder.mimeType || "").includes("mp4") ? ".mp4" : ".webm";
      const fd = new FormData();
      fd.append("file", blob, "dictation" + ext);
      fd.append("lang", state.lang);
      this.btn.classList.add("busy");
      this.status.textContent = "Transcribing with Whisper…";
      try {
        const r = await api("/api/transcribe", { method: "POST", body: fd });
        this.append(r.corrected_text || r.text);
        this.status.textContent = r.corrections?.length
          ? "Corrected: " + r.corrections.map((c) => `${c.from} → ${c.to}`).join(", ")
          : "";
      } catch (e) {
        toast(e.message, true);
        this.status.textContent = "";
      } finally {
        this.btn.classList.remove("busy");
      }
    };
    recorder.start();
    this.recorder = recorder;
  }
}

const dictations = Object.fromEntries($$(".dictation").map((el) => [el.dataset.id, new Dictation(el)]));

// ------------------------------------------------------------------ header controls
async function loadHealth() {
  try {
    state.health = await api("/api/health");
  } catch {
    state.health = { whisper: false, llm: false };
  }
  const h = state.health;
  const engineSel = $("#engine");
  $('option[value="whisper"]', engineSel).disabled = !h.whisper;
  $('option[value="browser"]', engineSel).disabled = !SpeechRecognition;
  if (!state.engine || (state.engine === "whisper" && !h.whisper) || (state.engine === "browser" && !SpeechRecognition)) {
    // local Whisper keeps patient audio on this machine, so prefer it when installed
    state.engine = h.whisper ? "whisper" : "browser";
  }
  engineSel.value = state.engine;

  const ai = $("#use-ai");
  ai.disabled = !h.llm;
  ai.checked = h.llm && state.useAI;
  const kb = h.knowledge_base || {};
  $("#status").innerHTML = `
    <span class="pill ${h.llm ? "on" : ""}" title="${h.llm ? "Claude model: " + esc(h.llm_model) : "Set ANTHROPIC_API_KEY to enable"}">AI ${h.llm ? "on" : "offline rules"}</span>
    <span class="pill" title="Local medical dictionary">${kb.condition ?? 0} dx · ${kb.medication ?? 0} drugs</span>`;
}

$("#lang").value = state.lang;
$("#lang").addEventListener("change", (e) => { state.lang = e.target.value; savePref("lang", state.lang); });
$("#engine").addEventListener("change", (e) => { state.engine = e.target.value; savePref("engine", state.engine); });
$("#use-ai").addEventListener("change", (e) => { state.useAI = e.target.checked; savePref("useAI", state.useAI ? "1" : "0"); });
const useAI = () => state.useAI && state.health?.llm;

// ------------------------------------------------------------------ patients
let searchTimer;
$("#patient-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchPatients(e.target.value), 200);
});
$("#patient-search").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const first = $("#patient-results li"); if (first) first.click(); }
});

async function searchPatients(q = "") {
  const list = $("#patient-results");
  try {
    const patients = await api("/api/patients?q=" + encodeURIComponent(q));
    list.innerHTML = patients.length
      ? patients.map((p) => `<li data-num="${esc(p.patient_number)}" class="${state.patient?.patient_number === p.patient_number ? "active" : ""}">
          <span>${esc(p.last_name.toUpperCase())} ${esc(p.first_name)}</span><span class="num">${esc(p.patient_number)}</span></li>`).join("")
      : `<li class="muted" style="cursor:default">${q ? "No patient found" : "No patients yet"}</li>`;
    $$("li[data-num]", list).forEach((li) => li.addEventListener("click", () => selectPatient(li.dataset.num)));
  } catch (e) { toast(e.message, true); }
}

function age(birth) {
  if (!birth) return null;
  const b = new Date(birth), n = new Date();
  return n.getFullYear() - b.getFullYear() - (n < new Date(n.getFullYear(), b.getMonth(), b.getDate()) ? 1 : 0);
}
const fmtDate = (d) => new Date(d).toLocaleDateString(state.lang === "fr" ? "fr-FR" : "en-GB", { day: "2-digit", month: "short", year: "numeric" });

async function selectPatient(number) {
  try {
    state.patient = await api("/api/patients/" + encodeURIComponent(number));
  } catch (e) { return toast(e.message, true); }
  renderPatient();
  resetConsultation();
  loadHistory();
  $$("#patient-results li").forEach((li) => li.classList.toggle("active", li.dataset.num === number));
}

function renderPatient() {
  const p = state.patient;
  $("#patient-card").classList.remove("hidden");
  $("#pc-name").textContent = `${p.first_name} ${p.last_name.toUpperCase()}`;
  const a = age(p.birth_date);
  $("#pc-meta").textContent = [p.sex, a !== null ? `${a} y/o` : "", p.phone].filter(Boolean).join(" · ");
  $("#pc-number").textContent = p.patient_number;
  $("#pc-allergies").classList.toggle("hidden", !p.allergies);
  $("#pc-allergies").textContent = "⚠ Allergies: " + p.allergies;
  $("#pc-chronic").classList.toggle("hidden", !p.chronic_conditions);
  $("#pc-chronic").textContent = "Chronic: " + p.chronic_conditions;
  $("#empty-state").classList.add("hidden");
  $("#workspace").classList.remove("hidden");
  $("#ws-sub").textContent = `${p.first_name} ${p.last_name.toUpperCase()} · ${p.patient_number}`;
}

$("#pc-number").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(state.patient.patient_number); toast("Patient number copied"); }
  catch { toast("Copy failed — select the number manually", true); }
});

async function loadHistory() {
  const ul = $("#history");
  const rows = await api(`/api/patients/${state.patient.patient_number}/consultations`).catch(() => []);
  ul.innerHTML = rows.length ? rows.map((c) => `
    <li data-id="${c.id}">
      <div class="h-date"><span>${fmtDate(c.created_at)}</span><span>${c.prescription.length} drug(s)${c.certificate ? " · certificate" : ""}</span></div>
      <div class="h-main">${esc(c.conditions.map((x) => x.name).join(", ") || c.diagnosis_text.slice(0, 60) || "—")}</div>
    </li>`).join("") : `<li class="muted" style="cursor:default">First visit</li>`;
  $$("li[data-id]", ul).forEach((li) => li.addEventListener("click", () => openConsultation(rows.find((r) => r.id == li.dataset.id))));
}

// patient dialog
const dlgPatient = $("#dlg-patient");
let editingPatient = false;
$("#btn-new-patient").addEventListener("click", () => openPatientDialog(false));
$("#btn-edit-patient").addEventListener("click", () => openPatientDialog(true));

function openPatientDialog(edit) {
  editingPatient = edit;
  const f = $("#form-patient");
  f.reset();
  $("#dlg-patient-title").textContent = edit ? "Edit patient" : "New patient";
  if (edit) for (const [k, v] of Object.entries(state.patient)) if (f.elements[k]) f.elements[k].value = v ?? "";
  dlgPatient.showModal();
}

dlgPatient.addEventListener("close", async () => {
  if (dlgPatient.returnValue !== "ok") return;
  const data = Object.fromEntries(new FormData($("#form-patient")));
  data.birth_date = data.birth_date || null;
  try {
    const p = editingPatient
      ? await api("/api/patients/" + state.patient.patient_number, { method: "PUT", body: data })
      : await api("/api/patients", { method: "POST", body: data });
    toast(editingPatient ? "Patient updated" : `Patient created — number ${p.patient_number}`);
    $("#patient-search").value = "";
    await searchPatients();
    if (editingPatient) { state.patient = p; renderPatient(); } else selectPatient(p.patient_number);
  } catch (e) { toast(e.message, true); }
});

// doctor dialog
const dlgDoctor = $("#dlg-doctor");
$("#btn-doctor").addEventListener("click", async () => {
  const f = $("#form-doctor");
  try {
    const d = await api("/api/doctor");
    for (const [k, v] of Object.entries(d)) if (f.elements[k]) f.elements[k].value = v ?? "";
    renderDoctorImages(d);
  } catch { /* empty profile */ }
  dlgDoctor.showModal();
});

// letterhead (entête) and signature images, uploaded as soon as they are picked
function renderDoctorImages(d) {
  $$(".img-field", dlgDoctor).forEach((field) => {
    const kind = field.dataset.kind, has = d["has_" + kind];
    const img = $("img", field);
    img.hidden = !has;
    if (has) img.src = `/api/doctor/${kind}?t=${Date.now()}`;
    $(".img-box .muted", field).hidden = has;
    $(".img-del", field).hidden = !has;
  });
}
$$(".img-field", dlgDoctor).forEach((field) => {
  const kind = field.dataset.kind, input = $("input[type=file]", field);
  input.addEventListener("change", async () => {
    if (!input.files[0]) return;
    const fd = new FormData();
    fd.append("file", input.files[0]);
    input.value = "";
    try {
      renderDoctorImages(await api("/api/doctor/" + kind, { method: "PUT", body: fd }));
      toast(kind === "letterhead" ? "Letterhead saved" : "Signature saved");
    } catch (e) { toast(e.message, true); }
  });
  $(".img-del", field).addEventListener("click", async () => {
    try {
      await api("/api/doctor/" + kind, { method: "DELETE" });
      renderDoctorImages(await api("/api/doctor"));
    } catch (e) { toast(e.message, true); }
  });
});
dlgDoctor.addEventListener("close", async () => {
  if (dlgDoctor.returnValue !== "ok") return;
  try {
    await api("/api/doctor", { method: "PUT", body: Object.fromEntries(new FormData($("#form-doctor"))) });
    toast("Doctor profile saved");
  } catch (e) { toast(e.message, true); }
});

// ------------------------------------------------------------------ steps
function showStep(step) {
  $$(".step").forEach((b) => b.classList.toggle("active", b.dataset.step === step));
  $$(".step-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== step));
  if (step === "review") renderReview();
  markDone();
}
$$(".step").forEach((b) => b.addEventListener("click", () => showStep(b.dataset.step)));
$$(".next").forEach((b) => b.addEventListener("click", () => showStep(b.dataset.next)));

function markDone() {
  const done = {
    diagnosis: state.conditions.length || $("#diag-text").value.trim(),
    prescription: state.items.length,
    certificate: $("#cert-on").checked,
  };
  $$(".step").forEach((b) => b.classList.toggle("done", !!done[b.dataset.step]));
}

// ------------------------------------------------------------------ 1. diagnosis
function renderChips() {
  $("#conditions").innerHTML = state.conditions.map((c, i) =>
    `<span class="chip">${c.code ? `<b>${esc(c.code)}</b>` : ""}${esc(c.name)}<button data-i="${i}" aria-label="Remove">×</button></span>`).join("");
  $("#symptoms").innerHTML = state.symptoms.map((s, i) =>
    `<span class="chip">${esc(s)}<button data-i="${i}" aria-label="Remove">×</button></span>`).join("");
  $$("#conditions button").forEach((b) => b.addEventListener("click", () => { state.conditions.splice(b.dataset.i, 1); renderChips(); }));
  $$("#symptoms button").forEach((b) => b.addEventListener("click", () => { state.symptoms.splice(b.dataset.i, 1); renderChips(); }));
  markDone();
}

function addCondition(c) {
  if (!c.name || state.conditions.some((x) => (x.code && x.code === c.code) || x.name === c.name)) return;
  state.conditions.push({ name: c.name, code: c.code || "" });
}
function addSymptom(name) {
  if (name && !state.symptoms.includes(name)) state.symptoms.push(name);
}

$("#btn-analyze").addEventListener("click", (e) => busy(e.target, async () => {
  const text = $("#diag-text").value.trim();
  if (!text) return toast("Dictate or type the diagnosis first", true);
  const r = await api("/api/ai/analyze-diagnosis", { method: "POST", body: { text, lang: state.lang, use_ai: useAI() } });
  r.conditions.forEach(addCondition);
  r.symptoms.forEach((s) => addSymptom(s.name));
  if (r.notes && !$("#diag-notes").value) $("#diag-notes").value = r.notes;
  const box = $("#diag-corrections");
  if (r.corrections?.length) {
    box.innerHTML = `Possible speech-recognition fixes: ${r.corrections.map((c) => `<b>${esc(c.from)}</b> → ${esc(c.to)}`).join(", ")}
      <button class="btn sm" id="apply-fix">Apply</button>`;
    box.classList.remove("hidden");
    $("#apply-fix").onclick = () => { $("#diag-text").value = r.corrected_text; box.classList.add("hidden"); };
  } else box.classList.add("hidden");
  renderChips();
  toast(`Detected ${r.conditions.length} diagnosis(es), ${r.symptoms.length} symptom(s) — ${r.engine === "claude" ? "Claude" : "offline engine"}`);
}));

// ------------------------------------------------------------------ 2. prescription
const RX_FIELDS = ["drug", "dosage", "form", "quantity", "frequency", "duration", "instructions"];

function renderItems() {
  const body = $("#rx-body");
  body.innerHTML = state.items.map((it, i) => `<tr data-i="${i}">
    ${RX_FIELDS.map((f) => `<td class="${f}"><input data-f="${f}" value="${esc(it[f])}" />${f === "drug" && it.drug_class ? `<span class="cls">${esc(it.drug_class)}</span>` : ""}</td>`).join("")}
    <td><button class="del" title="Remove">×</button></td></tr>`).join("");
  $$("tr", body).forEach((tr) => {
    const i = +tr.dataset.i;
    $$("input", tr).forEach((inp) => inp.addEventListener("input", () => { state.items[i][inp.dataset.f] = inp.value; }));
    $$("input[data-f=drug]", tr).forEach((inp) => inp.addEventListener("change", runSafety));
    $(".del", tr).addEventListener("click", () => { state.items.splice(i, 1); renderItems(); runSafety(); });
  });
  markDone();
}

function renderWarnings() {
  $("#rx-warnings").innerHTML = state.warnings.map((w) =>
    `<div class="warn ${w.severity}"><strong>${esc(w.severity)}</strong><span>${esc(w.message)}</span></div>`).join("");
}

async function runSafety() {
  if (!state.items.length) { state.warnings = []; return renderWarnings(); }
  try {
    const r = await api("/api/ai/safety-check", { method: "POST", body: { items: state.items, lang: state.lang, patient_number: state.patient?.patient_number } });
    state.warnings = r.warnings;
    renderWarnings();
  } catch { /* non-blocking */ }
}

$("#btn-parse").addEventListener("click", (e) => busy(e.target, async () => {
  const text = $("#rx-text").value.trim();
  if (!text) return toast("Dictate or type the prescription first", true);
  const r = await api("/api/ai/parse-prescription", { method: "POST",
    body: { text, lang: state.lang, use_ai: useAI(), patient_number: state.patient?.patient_number } });
  if (!r.items.length) return toast("No known drug recognised — add it manually below", true);
  state.items.push(...r.items);
  renderItems();
  await runSafety();
  toast(`${r.items.length} line(s) added — ${r.engine === "claude" ? "Claude" : "offline engine"}`);
}));

// ------------------------------------------------------------------ 3. certificate
function syncCert() {
  const on = $("#cert-on").checked;
  $("#cert-form").classList.toggle("disabled", !on);
  const type = $("#cert-type").value;
  $$(".f-days").forEach((el) => el.classList.toggle("hidden", type !== "sick_leave"));
  $(".f-sport").classList.toggle("hidden", type !== "fitness");
  markDone();
}
$("#cert-on").addEventListener("change", syncCert);
$("#cert-type").addEventListener("change", syncCert);

function setCertificate(cert) {
  $("#cert-on").checked = !!cert;
  if (cert) {
    $("#cert-type").value = cert.type || "sick_leave";
    $("#cert-days").value = cert.days || 0;
    $("#cert-start").value = cert.start_date || today();
    $("#cert-sport").value = cert.sport || "";
    $("#cert-remarks").value = cert.remarks || "";
  }
  syncCert();
}
const today = () => new Date().toISOString().slice(0, 10);

function getCertificate() {
  if (!$("#cert-on").checked) return null;
  return {
    type: $("#cert-type").value,
    days: +$("#cert-days").value || 0,
    start_date: $("#cert-start").value || null,
    sport: $("#cert-sport").value,
    remarks: $("#cert-remarks").value,
  };
}

// ------------------------------------------------------------------ smart dictation
$("#btn-structure").addEventListener("click", (e) => busy(e.target, async () => {
  const text = $(".dictation[data-id=smart] textarea").value.trim();
  if (!text) return toast("Dictate the consultation first", true);
  const r = await api("/api/ai/structure", { method: "POST",
    body: { text, lang: state.lang, use_ai: useAI(), patient_number: state.patient?.patient_number } });
  const d = r.diagnosis;
  if (r.sections?.diagnosis || d.summary) $("#diag-text").value = r.sections?.diagnosis || d.summary;
  if (d.notes) $("#diag-notes").value = d.notes;
  d.conditions.forEach(addCondition);
  d.symptoms.forEach((s) => addSymptom(typeof s === "string" ? s : s.name));
  if (r.sections?.prescription) $("#rx-text").value = r.sections.prescription;
  state.items.push(...r.prescription);
  state.warnings = r.warnings || [];
  if (r.certificate) setCertificate({ ...r.certificate, start_date: r.certificate.start_date || today() });
  renderChips(); renderItems(); renderWarnings();
  showStep("review");
  toast(`Filled by ${r.engine === "claude" ? "Claude" : "the offline engine"} — please review`);
}));

// ------------------------------------------------------------------ 4. review & save
function payload() {
  return {
    patient_number: state.patient.patient_number,
    language: state.lang,
    diagnosis_text: $("#diag-text").value,
    conditions: state.conditions,
    symptoms: state.symptoms,
    notes: $("#diag-notes").value,
    prescription: state.items.filter((i) => i.drug?.trim()),
    prescription_text: $("#rx-text").value,
    certificate: getCertificate(),
    transcript: $(".dictation[data-id=smart] textarea").value,
  };
}

const CERT_LABEL = { sick_leave: "Sick leave", fitness: "Fitness for sport", attendance: "Attendance", custom: "Custom" };

function renderReview() {
  const p = payload();
  const cert = p.certificate;
  $("#review").innerHTML = `
    <div class="review-block"><h3>1 · Diagnosis</h3>
      <div class="chips">${p.conditions.map((c) => `<span class="chip">${c.code ? `<b>${esc(c.code)}</b>` : ""}${esc(c.name)}</span>`).join("")}</div>
      ${p.symptoms.length ? `<p class="muted">Symptoms: ${esc(p.symptoms.join(", "))}</p>` : ""}
      ${p.diagnosis_text ? `<pre>${esc(p.diagnosis_text)}</pre>` : ""}
      ${p.notes ? `<p class="muted">${esc(p.notes)}</p>` : ""}
    </div>
    <div class="review-block"><h3>2 · Prescription</h3>
      ${p.prescription.length ? `<ol>${p.prescription.map((i) => `<li><b>${esc(i.drug)} ${esc(i.dosage)}</b> ${esc(i.form)} — ${esc([i.quantity, i.frequency, i.duration, i.instructions].filter(Boolean).join(" · "))}</li>`).join("")}</ol>` : `<p class="muted">No prescription</p>`}
      ${state.warnings.map((w) => `<div class="warn ${w.severity}"><strong>${esc(w.severity)}</strong><span>${esc(w.message)}</span></div>`).join("")}
    </div>
    <div class="review-block"><h3>3 · Certificate</h3>
      ${cert ? `<p><b>${CERT_LABEL[cert.type]}</b>${cert.type === "sick_leave" ? ` — ${cert.days} day(s) from ${esc(cert.start_date || "today")}` : ""}${cert.sport ? ` — ${esc(cert.sport)}` : ""}</p>
      ${cert.remarks ? `<p class="muted">${esc(cert.remarks)}</p>` : ""}` : `<p class="muted">No certificate</p>`}
    </div>`;
}

$("#btn-save").addEventListener("click", (e) => busy(e.target, async () => {
  const p = payload();
  if (!p.conditions.length && !p.diagnosis_text.trim() && !p.prescription.length)
    return toast("Nothing to save yet", true);
  const c = state.consultationId
    ? await api("/api/consultations/" + state.consultationId, { method: "PUT", body: p })
    : await api("/api/consultations", { method: "POST", body: p });
  state.consultationId = c.id;
  $("#ws-title").textContent = `Consultation #${c.id}`;
  $("#save-msg").textContent = "Saved ✓";
  showPdfLinks(c);
  loadHistory();
  toast("Consultation saved");
}));

// Download the ordonnance / certificate as currently filled in, before (or without) saving
$$("[data-preview]").forEach((btn) => btn.addEventListener("click", () => busy(btn, async () => {
  const kind = btn.dataset.preview, p = payload();
  if (kind === "prescription" && !p.prescription.length) return toast("Add at least one drug first", true);
  if (kind === "certificate" && !p.certificate) return toast("Tick “Include a certificate” first", true);
  const res = await api(`/api/preview/${kind}.pdf`, { method: "POST", body: p, raw: true });
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "")?.[1] || kind + ".pdf";
  const url = URL.createObjectURL(await res.blob());
  Object.assign(document.createElement("a"), { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
})));

function showPdfLinks(c) {
  const rx = $("#btn-pdf-rx"), cert = $("#btn-pdf-cert");
  rx.href = `/api/consultations/${c.id}/prescription.pdf`;
  cert.href = `/api/consultations/${c.id}/certificate.pdf`;
  rx.classList.toggle("hidden", !c.prescription.length);
  cert.classList.toggle("hidden", !c.certificate);
}

// ------------------------------------------------------------------ load / reset
function resetConsultation() {
  Object.values(dictations).forEach((d) => d.recording && d.stop());
  state.consultationId = null;
  state.conditions = []; state.symptoms = []; state.items = []; state.warnings = [];
  $$(".dictation textarea").forEach((t) => (t.value = ""));
  $("#diag-notes").value = "";
  $("#diag-corrections").classList.add("hidden");
  setCertificate(null);
  $("#cert-start").value = today();
  $("#cert-days").value = 3;
  $("#ws-title").textContent = "New consultation";
  $("#save-msg").textContent = "";
  $("#btn-pdf-rx").classList.add("hidden");
  $("#btn-pdf-cert").classList.add("hidden");
  renderChips(); renderItems(); renderWarnings();
  showStep("diagnosis");
}
$("#btn-reset").addEventListener("click", resetConsultation);

function openConsultation(c) {
  resetConsultation();
  state.consultationId = c.id;
  state.lang = c.language; $("#lang").value = c.language;
  $("#diag-text").value = c.diagnosis_text;
  $("#diag-notes").value = c.notes;
  $("#rx-text").value = c.prescription_text;
  $(".dictation[data-id=smart] textarea").value = c.transcript;
  state.conditions = c.conditions.map((x) => ({ name: x.name, code: x.code || "" }));
  state.symptoms = [...c.symptoms];
  state.items = c.prescription.map((x) => ({ ...x }));
  setCertificate(c.certificate);
  $("#ws-title").textContent = `Consultation #${c.id} · ${fmtDate(c.created_at)}`;
  renderChips(); renderItems(); runSafety();
  showPdfLinks(c);
  showStep("review");
}

// ------------------------------------------------------------------ autocomplete
$$(".ac").forEach((ac) => {
  const input = $("input", ac), list = $(".ac-list", ac), type = ac.dataset.type;
  let timer, results = [], sel = -1;
  const pick = (r) => {
    if (type === "condition") { addCondition({ name: r.name, code: r.code }); renderChips(); }
    else if (type === "symptom") { addSymptom(r.name); renderChips(); }
    else {
      state.items.push({ drug: r.name, drug_class: r.class || "", dosage: "", form: "", quantity: "", frequency: "", duration: "", instructions: "" });
      renderItems(); runSafety();
      setTimeout(() => $(`#rx-body tr:last-child input[data-f=dosage]`)?.focus());
    }
    input.value = ""; list.innerHTML = ""; results = [];
  };
  const draw = () => {
    list.innerHTML = results.map((r, i) => `<li class="${i === sel ? "sel" : ""}" data-i="${i}">${r.code ? `<b>${esc(r.code)}</b> ` : ""}${esc(r.name)}${r.class ? `<small>${esc(r.class)}</small>` : ""}</li>`).join("");
    $$("li", list).forEach((li) => li.addEventListener("mousedown", (e) => { e.preventDefault(); pick(results[li.dataset.i]); }));
  };
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { list.innerHTML = ""; results = []; return; }
    timer = setTimeout(async () => {
      results = await api(`/api/terms/search?q=${encodeURIComponent(q)}&lang=${state.lang}&type=${type}`).catch(() => []);
      sel = 0; draw();
    }, 150);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, results.length - 1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (results[sel]) pick(results[sel]);
      else if (input.value.trim() && type !== "medication") pick({ name: input.value.trim(), code: "" }); // free text
      else if (input.value.trim()) pick({ name: input.value.trim() });
    } else if (e.key === "Escape") { list.innerHTML = ""; }
  });
  input.addEventListener("blur", () => setTimeout(() => (list.innerHTML = ""), 150));
});

// ------------------------------------------------------------------ boot
loadHealth();
searchPatients();
resetConsultation();
