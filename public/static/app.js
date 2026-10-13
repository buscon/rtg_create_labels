// Participant interface of the repertory grid elicitation tool (design file 07).
// Plain JavaScript, no build step. Talks to api.php.

import { TEXT } from "./i18n.js";
import { preload, embedPoles } from "./embedder.js";

const app = document.getElementById("app");
const params = new URLSearchParams(window.location.search);
const S = {
  cfg: null,
  lang: "de",
  pid: null,
  mode: params.get("mode") === "interviewer" ? "interviewer" : "online",
  invited: ["online", "in_person"].includes(params.get("invited")) ? params.get("invited") : null,
  token: null,
  code: null,          // group code from the invitation link (?c=...), design file 14
  hpTrials: [],
  practiceDone: false,
};

// ------------------------------------------------------------------ helpers

const t = (key, vars = {}) =>
  (TEXT[S.lang][key] ?? TEXT.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Text that follows the language switch: rendered with data-i18n, so relabel()
// can translate the current screen in place without re-rendering it (which
// would lose audio progress or typed text).
const tx = (key, vars = {}) =>
  `<span data-i18n="${key}" data-vars="${esc(JSON.stringify(vars))}">${esc(t(key, vars))}</span>`;

function setText(el, key, vars = {}) {
  el.dataset.i18n = key;
  el.dataset.vars = JSON.stringify(vars);
  el.textContent = t(key, vars);
}

function relabel() {
  app.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n, JSON.parse(el.dataset.vars || "{}"));
  });
  const prog = document.getElementById("progress");
  if (prog.dataset.i18n) prog.textContent = t(prog.dataset.i18n, JSON.parse(prog.dataset.vars || "{}"));
  app.querySelectorAll("[data-prompt]").forEach((el) => { el.textContent = S.cfg.prompt[S.lang]; });
  const ivq = document.getElementById("iv-questions");
  if (ivq) ivq.innerHTML = (S.cfg.followup_questions[S.lang] || []).map((q) => `<li>${esc(q)}</li>`).join("");
}

const store = {
  get(k) { try { return window.localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { window.localStorage.setItem(k, v); } catch { /* ignore */ } },
  del(k) { try { window.localStorage.removeItem(k); } catch { /* ignore */ } },
};

async function api(route, { method = "GET", body, query = {} } = {}) {
  const q = new URLSearchParams({ r: route, ...query });
  const headers = { "Content-Type": "application/json" };
  if (S.token) headers["X-Interviewer-Token"] = S.token;
  const res = await fetch(`api.php?${q}`, {
    method, headers, body: body ? JSON.stringify(body) : undefined, cache: "no-store",
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function render(html) {
  app.innerHTML = html;
  window.scrollTo(0, 0);
  const h = app.querySelector("h1");
  if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
}

function showError(e) {
  console.error(e);
  render(`<div class="panel"><h1>${tx("title")}</h1><p class="note">${tx("error")}</p>
    <p class="muted">${esc(e.message || "")}</p></div>`);
}

function setProgress(key, vars = {}) {
  const el = document.getElementById("progress");
  if (!key) { delete el.dataset.i18n; el.textContent = ""; return; }
  setText(el, key, vars);
}

const LANG_NAMES = { de: "Deutsch", en: "English" };

/** Order: ?lang= in the link, earlier choice in this browser, browser language, configured default. */
function initialLanguage() {
  const ok = (l) => l && S.cfg.languages.includes(l);
  const url = params.get("lang");
  if (ok(url)) return url;
  const saved = store.get("rgt_lang");
  if (ok(saved)) return saved;
  for (const l of navigator.languages || [navigator.language || ""]) {
    const short = l.slice(0, 2).toLowerCase();
    if (ok(short)) return short;
  }
  return S.cfg.default_language;
}

function applyLanguage() {
  document.documentElement.lang = S.lang;
  document.title = t("title");
  document.getElementById("brand").textContent = t("title");
  document.querySelectorAll("#lang-switch button").forEach((b) => {
    b.setAttribute("aria-pressed", b.dataset.lang === S.lang ? "true" : "false");
  });
}

/** Switch the language at any point; the current screen is translated in place. */
function switchLanguage(lang) {
  if (lang === S.lang) return;
  S.lang = lang;
  store.set("rgt_lang", lang);
  applyLanguage();
  relabel();
  document.querySelectorAll("#credits-body p[data-lang]").forEach((p) => { p.hidden = p.dataset.lang !== S.lang; });
}

function isMobile() {
  const coarse = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent) || (coarse && window.innerWidth < 900);
}

// ------------------------------------------------------------------ start

async function init() {
  try {
    S.cfg = await api("config");
  } catch (e) { return showError(e); }
  S.lang = initialLanguage();
  const sw = document.getElementById("lang-switch");
  sw.innerHTML = S.cfg.languages.map((l) =>
    `<button type="button" data-lang="${l}" lang="${l}">${esc(LANG_NAMES[l] || l.toUpperCase())}</button>`).join("");
  sw.querySelectorAll("button").forEach((b) => { b.onclick = () => switchLanguage(b.dataset.lang); });
  sw.hidden = S.cfg.languages.length < 2;
  applyLanguage();

  if (S.cfg.block_mobile && isMobile() && S.mode !== "interviewer") {
    return render(`<div class="panel"><h1>${tx("mobile_title")}</h1><p>${tx("mobile_text")}</p></div>`);
  }

  // the group code is kept in this browser, so that reloading the page without ?c= still works
  S.code = params.get("c") || store.get("rgt_code");
  if (params.get("c")) store.set("rgt_code", params.get("c"));

  const saved = store.get("rgt_pid");
  if (saved && S.mode === "online") {
    try {
      const st = await api("state", { query: { pid: saved } });
      if (!st.finished && (st.headphone_passed || !S.cfg.headphone_check.enabled)) {
        return showResume(saved, st);
      }
    } catch { /* unknown participant: start again */ }
    store.del("rgt_pid");
  }
  if (S.cfg.require_code && S.mode === "online") {
    try {
      const a = await api("access", { query: { c: S.code || "" } });
      if (a.status !== "ok") return showInactive(a.status);
    } catch (e) { return showError(e); }
  }
  showWelcome();
}

/** The invitation link cannot be used (missing, wrong, expired or used up), or too many sessions from one IP. */
function showInactive(status) {
  const key = { missing: "code_missing", invalid: "code_invalid", expired: "code_expired",
                used_up: "code_used_up", too_many_sessions: "too_many" }[status] || "code_invalid";
  render(`<div class="panel"><h1>${tx(status === "too_many_sessions" ? "too_many_title" : "inactive_title")}</h1>
    <p>${tx(key)}</p></div>`);
}

function showResume(pid, st) {
  render(`<div class="panel"><h1>${tx("resume_title")}</h1><p>${tx("resume_text")}</p>
    <div class="actions"><button class="primary" id="yes">${tx("resume_yes")}</button>
    <button id="no">${tx("resume_no")}</button></div></div>`);
  document.getElementById("yes").onclick = () => {
    S.pid = pid; S.practiceDone = true;
    startEmbedding();
    loadNext();
  };
  document.getElementById("no").onclick = () => { store.del("rgt_pid"); showWelcome(); };
}

function showWelcome() {
  const iv = S.mode === "interviewer" ? `
    <h2>${tx("interviewer_title")}</h2><p class="muted">${tx("interviewer_text")}</p>
    <label class="block" for="pw">${tx("interviewer_password")}</label>
    <input type="password" id="pw" autocomplete="off">` : "";
  render(`<div class="panel"><h1>${tx("welcome_title")}</h1>
    <p>${tx("welcome_text")}</p><p>${tx("consent_text")}</p>
    <label class="check"><input type="checkbox" id="consent"> <span>${tx("consent_check")}</span></label>
    ${iv}
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go" disabled>${tx("start")}</button></div></div>`);
  const consent = document.getElementById("consent");
  const go = document.getElementById("go");
  consent.onchange = () => { go.disabled = !consent.checked; };
  go.onclick = () => {
    if (S.mode === "interviewer") {
      S.token = document.getElementById("pw").value.trim();
      if (!S.token) return;
    }
    startEmbedding();
    showAbout();
  };
}

function startEmbedding() {
  if (S.cfg.similarity.backend === "embedding") preload(S.cfg.similarity.model);
}

function showAbout() {
  render(`<div class="panel"><h1>${tx("about_title")}</h1>
    <p>${tx("setting_text")}</p>
    <label class="block">${tx("device_q")}</label>
    <div class="radios">
      <label><input type="radio" name="device" value="headphones"> ${tx("device_headphones")}</label>
      <label><input type="radio" name="device" value="speakers"> ${tx("device_speakers")}</label>
      <label><input type="radio" name="device" value="builtin"> ${tx("device_builtin")}</label>
    </div>
    <label class="block" for="hp">${tx("device_model_q")}</label>
    <input type="text" id="hp" maxlength="200" autocomplete="off">
    <label class="block">${tx("environment_q")}</label>
    <div class="radios">
      <label><input type="radio" name="env" value="quiet"> ${tx("environment_quiet")}</label>
      <label><input type="radio" name="env" value="some_noise"> ${tx("environment_some")}</label>
      <label><input type="radio" name="env" value="noisy"> ${tx("environment_noisy")}</label>
    </div>
    <label class="block">${tx("hearing_q")}</label>
    <div class="radios">
      <label><input type="radio" name="hear" value="no"> ${tx("hearing_no")}</label>
      <label><input type="radio" name="hear" value="yes"> ${tx("hearing_yes")}</label>
      <label><input type="radio" name="hear" value="na"> ${tx("hearing_na")}</label>
    </div>
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go">${tx("next")}</button></div></div>`);
  document.getElementById("go").onclick = async (ev) => {
    const hear = app.querySelector("input[name=hear]:checked");
    const device = app.querySelector("input[name=device]:checked");
    const env = app.querySelector("input[name=env]:checked");
    if (!device || !env) {
      const m = document.getElementById("msg");
      setText(m, "setting_missing"); m.hidden = false;
      return;
    }
    ev.target.disabled = true;
    try {
      const res = await api("session", { method: "POST", body: {
        language: S.lang, invited_mode: S.invited, access_code: S.code,
        listening_device: device.value, listening_environment: env.value,
        headphones_model: document.getElementById("hp").value,
        hearing_problems: hear ? hear.value : null,
      } });
      S.pid = res.participant_id;
      S.hpTrials = res.headphone_trials || [];
      if (S.mode === "online") store.set("rgt_pid", S.pid);
      showVolume();
    } catch (e) {
      if (e.status === 403 && e.message.startsWith("access_code_")) return showInactive(e.message.slice(12));
      if (e.status === 429) return showInactive("too_many_sessions");
      if (e.status === 403) {
        const m = document.getElementById("msg");
        setText(m, "interviewer_wrong"); m.hidden = false;
        S.token = null;
        setTimeout(showWelcome, 1500);
      } else showError(e);
    }
  };
}

// ------------------------------------------------------------------ volume and headphone check

function showVolume() {
  render(`<div class="panel"><h1>${tx("volume_title")}</h1><p>${tx("volume_text")}</p>
    <div class="actions"><button id="play">${tx("play")}</button>
    <span class="spacer"></span><button class="primary" id="go" disabled>${tx("volume_done")}</button></div>
    <p class="note" id="msg" hidden></p></div>`);
  const audio = new Audio(S.cfg.calibration_url);
  audio.loop = true;
  const play = document.getElementById("play");
  const go = document.getElementById("go");
  // the button follows what the audio element really does
  audio.onplaying = () => { setText(play, "pause"); go.disabled = false; };
  audio.onpause = () => setText(play, "play");
  audio.onerror = () => audioFailed();
  play.onclick = () => {
    if (audio.paused) audio.play().catch(audioFailed);
    else audio.pause();
  };
  go.onclick = () => {
    audio.pause();
    if (S.cfg.headphone_check.enabled) showHeadphone(0, []); else showInstructions();
  };
}

/** A sound could not be loaded or played: say so instead of failing silently. */
function audioFailed(e) {
  if (e) console.error(e);
  const m = document.getElementById("msg");
  if (m) { setText(m, "audio_error"); m.hidden = false; }
}

function showHeadphone(i, answers) {
  const trial = S.hpTrials[i];
  render(`<div class="panel"><h1>${tx("hp_title")}</h1><p>${tx("hp_text")}</p>
    <p class="muted">${tx("hp_trial", { n: i + 1, total: S.hpTrials.length })}</p>
    <div class="actions"><button class="primary" id="play">${tx("play")}</button></div>
    <p id="which" hidden>${tx("hp_which")}</p>
    <div class="hp-choices" id="choices" hidden>
      ${[1, 2, 3].map((n) => `<button data-n="${n}">${n}</button>`).join("")}
    </div></div>`);
  const audio = new Audio(trial.url);
  const play = document.getElementById("play");
  play.onclick = () => { play.disabled = true; audio.play(); };
  audio.onended = () => {
    document.getElementById("which").hidden = false;
    document.getElementById("choices").hidden = false;
  };
  app.querySelectorAll("#choices button").forEach((b) => {
    b.onclick = async () => {
      const next = [...answers, Number(b.dataset.n)];
      if (i + 1 < S.hpTrials.length) return showHeadphone(i + 1, next);
      try {
        const res = await api("headphone", { method: "POST", query: { pid: S.pid }, body: { answers: next } });
        if (res.passed) showInstructions();
        else {
          store.del("rgt_pid");
          render(`<div class="panel"><h1>${tx("hp_fail_title")}</h1><p>${tx("hp_fail_text")}</p></div>`);
        }
      } catch (e) { showError(e); }
    };
  });
}

function showInstructions() {
  render(`<div class="panel"><h1>${tx("instr_title")}</h1><p>${tx("instr_text")}</p>
    <p class="example">${tx("instr_example")}</p><p>${tx("instr_practice")}</p>
    <div class="actions"><button class="primary" id="go">${tx("start")}</button></div></div>`);
  document.getElementById("go").onclick = () => loadNext();
}

// ------------------------------------------------------------------ triads

async function loadNext() {
  render(`<p class="muted"><span class="spinner"></span>${tx("loading")}</p>`);
  try {
    const res = await api("next", { query: { pid: S.pid } });
    if (res.done) return showEnd();
    if (!res.triad.is_practice && !S.practiceDone) {
      S.practiceDone = true;
      return showAfterPractice(res.triad);
    }
    showTriad(res.triad);
  } catch (e) { showError(e); }
}

function showAfterPractice(triad) {
  render(`<div class="panel"><h1>${tx("after_practice_title")}</h1><p>${tx("after_practice_text")}</p>
    <div class="actions"><button class="primary" id="go">${tx("start")}</button></div></div>`);
  document.getElementById("go").onclick = () => showTriad(triad);
}

function showTriad(triad) {
  const shownAt = performance.now();
  const L = S.cfg.labels;
  const [hKey, hVars] = triad.is_practice ? ["practice_label", {}] : ["triad_label", { n: triad.n_done + 1 }];
  setProgress(hKey, hVars);
  render(`<h1>${tx(hKey, hVars)}</h1>
    <p data-prompt>${esc(S.cfg.prompt[S.lang])}</p>
    <div class="clips">
      ${triad.clips.map((c) => `
        <div class="clip" data-pos="${c.position}">
          <div class="letter">${c.position}</div>
          <button class="playbtn">${tx("play")}</button>
          <div class="bar"><span></span></div>
          <div class="status"></div>
          <button class="selbtn" disabled>${tx("select")}</button>
        </div>`).join("")}
    </div>
    <p class="note" id="listen">${tx("listen_first")}</p>
    <div class="panel" id="form" hidden>
      <div class="labels">
        <label class="block" for="sim" id="sim-label"></label>
        <input type="text" id="sim" maxlength="${L.max_chars}" autocomplete="off">
        <label class="block" for="con" id="con-label"></label>
        <input type="text" id="con" maxlength="${L.max_chars}" autocomplete="off">
      </div>
      <p class="note" id="msg" hidden></p>
    </div>
    <div class="actions">
      <button class="primary" id="submit" disabled>${tx("submit")}</button>
      <span class="spacer"></span>
      <button id="nodiff" disabled>${tx("no_difference")}</button>
    </div>`);

  const players = {};
  const heard = new Set();
  const selected = [];

  const logEvent = (pos, event) => api("play", { method: "POST", body: {
    participant_id: S.pid, triad_id: triad.triad_id, position: pos, event,
    at_ms: Math.round(performance.now() - shownAt),
  } });

  const pauseOthers = (except) => {
    for (const [pos, p] of Object.entries(players)) {
      if (pos !== except && !p.audio.paused) p.audio.pause();
    }
  };

  const updateState = () => {
    const allHeard = !S.cfg.require_full_play || heard.size === 3;
    document.getElementById("listen").hidden = allHeard;
    app.querySelectorAll(".selbtn").forEach((b) => { b.disabled = !allHeard; });
    document.getElementById("nodiff").disabled = !allHeard;
    const ready = allHeard && selected.length === 2;
    document.getElementById("form").hidden = !ready;
    document.getElementById("submit").disabled = !ready;
    if (ready) {
      const odd = ["A", "B", "C"].find((p) => !selected.includes(p));
      const [a, b] = [...selected].sort();
      setText(document.getElementById("sim-label"), "sim_label", { a, b });
      setText(document.getElementById("con-label"), "con_label", { c: odd });
    }
    app.querySelectorAll(".clip").forEach((el) => {
      const sel = selected.includes(el.dataset.pos);
      el.classList.toggle("selected", sel);
      setText(el.querySelector(".selbtn"), sel ? "selected" : "select");
      el.querySelector(".selbtn").setAttribute("aria-pressed", sel ? "true" : "false");
    });
  };

  app.querySelectorAll(".clip").forEach((el) => {
    const pos = el.dataset.pos;
    const clip = triad.clips.find((c) => c.position === pos);
    const audio = new Audio(clip.url);
    audio.preload = "auto";
    const btn = el.querySelector(".playbtn");
    const bar = el.querySelector(".bar > span");
    const status = el.querySelector(".status");
    players[pos] = { audio };
    audio.onerror = () => { setText(status, "audio_error"); status.classList.add("err"); };
    btn.onclick = () => {
      if (audio.paused) {
        pauseOthers(pos);
        audio.play().catch(() => { setText(status, "audio_error"); status.classList.add("err"); });
      } else audio.pause();
    };
    audio.onplay = () => { setText(btn, "pause"); logEvent(pos, "play").catch(() => {}); };
    audio.onpause = () => {
      setText(btn, "play");
      if (!audio.ended) logEvent(pos, "pause").catch(() => {});
    };
    audio.ontimeupdate = () => {
      if (audio.duration) bar.style.width = `${(100 * audio.currentTime) / audio.duration}%`;
    };
    audio.onended = async () => {
      setText(btn, "play");
      try { await logEvent(pos, "ended"); } catch (e) { return showError(e); }
      heard.add(pos);
      setText(status, "heard_ok");
      status.classList.add("ok");
      if (!S.cfg.allow_replay) btn.disabled = true;
      updateState();
    };
    el.querySelector(".selbtn").onclick = () => {
      const i = selected.indexOf(pos);
      if (i >= 0) selected.splice(i, 1);
      else { if (selected.length === 2) selected.shift(); selected.push(pos); }
      updateState();
    };
  });

  const stopAll = () => Object.values(players).forEach((p) => p.audio.pause());

  const send = async (payload, poles) => {
    stopAll();
    document.getElementById("submit").disabled = true;
    document.getElementById("nodiff").disabled = true;
    render(`<p class="muted"><span class="spinner"></span>${tx("saving")}</p>`);
    try {
      const res = await api("response", { method: "POST", body: {
        participant_id: S.pid, triad_id: triad.triad_id, rt_ms: Math.round(performance.now() - shownAt),
        language: S.lang, ...payload,
      } });
      if (S.mode === "interviewer" && res.construct_id && !triad.is_practice) {
        return showInterviewerPanel(res, poles);
      }
      if (res.done) return showEnd();
      loadNext();
    } catch (e) { showError(e); }
  };

  document.getElementById("submit").onclick = async () => {
    const sim = document.getElementById("sim").value.trim();
    const con = document.getElementById("con").value.trim();
    const words = (s) => s.split(/\s+/).filter(Boolean).length;
    if (words(sim) < L.min_words || words(con) < L.min_words) {
      const m = document.getElementById("msg"); setText(m, "too_short"); m.hidden = false;
      return;
    }
    let embedding = null;
    if (S.cfg.similarity.backend === "embedding" && !triad.is_practice) {
      document.getElementById("submit").disabled = true;
      document.getElementById("submit").innerHTML = `<span class="spinner"></span>${tx("saving")}`;
      embedding = await embedPoles(sim, con, S.cfg.similarity.model, S.cfg.similarity.wait_s);
    }
    send({ pair: [...selected], similarity_pole: sim, contrast_pole: con, no_difference: false, embedding },
         { sim, con, pair: [...selected] });
  };
  document.getElementById("nodiff").onclick = () => send({ no_difference: true }, null);
  updateState();
}

// ------------------------------------------------------------------ interviewer panel (design file 11)

function showInterviewerPanel(res, poles) {
  const qs = (S.cfg.followup_questions[S.lang] || []).map((q) => `<li>${esc(q)}</li>`).join("");
  const earlier = (res.earlier_constructs || []).map((c) =>
    `<option value="${c.construct_id}">${esc(c.similarity_pole)} ↔ ${esc(c.contrast_pole)}</option>`).join("");
  render(`<div class="panel iv"><h1>${tx("iv_title")}</h1>
    <p class="muted">${tx("iv_answer")}:</p>
    <p class="answer">${esc(poles.sim)} ↔ ${esc(poles.con)}</p>
    ${qs ? `<p class="muted">${tx("iv_questions")}</p><ul id="iv-questions">${qs}</ul>` : ""}
    <label class="block" for="rs">${tx("iv_rev_sim")}</label><input type="text" id="rs" maxlength="300">
    <label class="block" for="rc">${tx("iv_rev_con")}</label><input type="text" id="rc" maxlength="300">
    <label class="block">${tx("iv_judgement")}</label>
    <div class="radios">
      <label><input type="radio" name="j" value="new"> ${tx("iv_new")}</label>
      ${earlier ? `<label><input type="radio" name="j" value="same"> ${tx("iv_same")}</label>` : ""}
    </div>
    ${earlier ? `<select id="same" disabled>${earlier}</select>` : ""}
    <label class="block" for="note">${tx("iv_note")}</label><textarea id="note" rows="2" maxlength="2000"></textarea>
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go">${tx("iv_continue")}</button></div></div>`);
  const sameSel = document.getElementById("same");
  app.querySelectorAll("input[name=j]").forEach((r) => {
    r.onchange = () => { if (sameSel) sameSel.disabled = r.value !== "same" || !r.checked; };
  });
  document.getElementById("go").onclick = async (ev) => {
    const j = app.querySelector("input[name=j]:checked");
    if (!j) { const m = document.getElementById("msg"); setText(m, "iv_choose"); m.hidden = false; return; }
    ev.target.disabled = true;
    try {
      await api("judgement", { method: "POST", body: {
        participant_id: S.pid, construct_id: res.construct_id, judged_new: j.value === "new",
        same_as: j.value === "same" && sameSel ? Number(sameSel.value) : null,
        revised_similarity_pole: document.getElementById("rs").value,
        revised_contrast_pole: document.getElementById("rc").value,
        note: document.getElementById("note").value,
      } });
      if (res.done) showEnd(); else loadNext();
    } catch (e) { showError(e); }
  };
}

function showEnd() {
  store.del("rgt_pid");
  setProgress(null);
  render(`<div class="panel"><h1>${tx("end_title")}</h1><p>${tx("end_text")}</p>
    <div id="credits" class="credits" hidden><h2>${tx("credits")}</h2><div id="credits-body"></div></div></div>`);
  // credits for the recordings (CC BY), shown only after the task so they cannot bias the answers.
  // credits.html has one paragraph in English and one in German, then the list of recordings.
  fetch("credits.html", { cache: "no-store" })
    .then((r) => (r.ok ? r.text() : Promise.reject()))
    .then((html) => {
      const doc = new DOMParser().parseFromString(html, "text/html");
      const paras = [...doc.querySelectorAll(".panel > p")];
      const list = doc.querySelector(".panel ul");
      const body = document.getElementById("credits-body");
      paras.slice(0, 2).forEach((p, i) => {
        const el = document.importNode(p, true);
        el.dataset.lang = i === 0 ? "en" : "de";
        el.hidden = el.dataset.lang !== S.lang;
        body.appendChild(el);
      });
      if (list) body.appendChild(document.importNode(list, true));
      body.querySelectorAll("a").forEach((a) => { a.target = "_blank"; a.rel = "noopener"; });
      document.getElementById("credits").hidden = false;
    })
    .catch(() => {});
}

init();
