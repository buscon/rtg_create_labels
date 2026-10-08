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
  hpTrials: [],
  practiceDone: false,
};

// ------------------------------------------------------------------ helpers

const t = (key, vars = {}) =>
  (TEXT[S.lang][key] ?? TEXT.en[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");

const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
  render(`<div class="panel"><h1>${esc(t("title"))}</h1><p class="note">${esc(t("error"))}</p>
    <p class="muted">${esc(e.message || "")}</p></div>`);
}

function setProgress(text) { document.getElementById("progress").textContent = text || ""; }

function applyLanguage() {
  document.documentElement.lang = S.lang;
  document.title = t("title");
  document.getElementById("brand").textContent = t("title");
  document.getElementById("lang-label").textContent = t("lang_label");
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
  const urlLang = params.get("lang");
  S.lang = S.cfg.languages.includes(urlLang) ? urlLang : S.cfg.default_language;
  const sel = document.getElementById("lang");
  sel.innerHTML = S.cfg.languages.map((l) => `<option value="${l}">${l.toUpperCase()}</option>`).join("");
  sel.value = S.lang;
  sel.addEventListener("change", () => { S.lang = sel.value; applyLanguage(); showWelcome(); });
  document.getElementById("lang-wrap").hidden = S.cfg.languages.length < 2;
  applyLanguage();

  if (S.cfg.block_mobile && isMobile() && S.mode !== "interviewer") {
    return render(`<div class="panel"><h1>${esc(t("mobile_title"))}</h1><p>${esc(t("mobile_text"))}</p></div>`);
  }

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
  showWelcome();
}

function showResume(pid, st) {
  render(`<div class="panel"><h1>${esc(t("resume_title"))}</h1><p>${esc(t("resume_text"))}</p>
    <div class="actions"><button class="primary" id="yes">${esc(t("resume_yes"))}</button>
    <button id="no">${esc(t("resume_no"))}</button></div></div>`);
  document.getElementById("yes").onclick = () => {
    S.pid = pid; S.lang = st.language; S.practiceDone = true; applyLanguage();
    document.getElementById("lang-wrap").hidden = true;
    startEmbedding();
    loadNext();
  };
  document.getElementById("no").onclick = () => { store.del("rgt_pid"); showWelcome(); };
}

function showWelcome() {
  const iv = S.mode === "interviewer" ? `
    <h2>${esc(t("interviewer_title"))}</h2><p class="muted">${esc(t("interviewer_text"))}</p>
    <label class="block" for="pw">${esc(t("interviewer_password"))}</label>
    <input type="password" id="pw" autocomplete="off">` : "";
  render(`<div class="panel"><h1>${esc(t("welcome_title"))}</h1>
    <p>${esc(t("welcome_text"))}</p><p>${esc(t("consent_text"))}</p>
    <label class="check"><input type="checkbox" id="consent"> <span>${esc(t("consent_check"))}</span></label>
    ${iv}
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go" disabled>${esc(t("start"))}</button></div></div>`);
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
  render(`<div class="panel"><h1>${esc(t("about_title"))}</h1>
    <label class="block" for="hp">${esc(t("headphones_q"))}</label>
    <input type="text" id="hp" maxlength="200" autocomplete="off">
    <label class="block">${esc(t("hearing_q"))}</label>
    <div class="radios">
      <label><input type="radio" name="hear" value="no"> ${esc(t("hearing_no"))}</label>
      <label><input type="radio" name="hear" value="yes"> ${esc(t("hearing_yes"))}</label>
      <label><input type="radio" name="hear" value="na"> ${esc(t("hearing_na"))}</label>
    </div>
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go">${esc(t("next"))}</button></div></div>`);
  document.getElementById("go").onclick = async (ev) => {
    ev.target.disabled = true;
    const hear = app.querySelector("input[name=hear]:checked");
    try {
      const res = await api("session", { method: "POST", body: {
        language: S.lang, invited_mode: S.invited,
        headphones_model: document.getElementById("hp").value,
        hearing_problems: hear ? hear.value : null,
      } });
      S.pid = res.participant_id;
      S.hpTrials = res.headphone_trials || [];
      if (S.mode === "online") store.set("rgt_pid", S.pid);
      document.getElementById("lang-wrap").hidden = true;
      showVolume();
    } catch (e) {
      if (e.status === 403) {
        const m = document.getElementById("msg");
        m.textContent = t("interviewer_wrong"); m.hidden = false;
        S.token = null;
        setTimeout(showWelcome, 1500);
      } else showError(e);
    }
  };
}

// ------------------------------------------------------------------ volume and headphone check

function showVolume() {
  render(`<div class="panel"><h1>${esc(t("volume_title"))}</h1><p>${esc(t("volume_text"))}</p>
    <div class="actions"><button id="play">${esc(t("play"))}</button>
    <span class="spacer"></span><button class="primary" id="go" disabled>${esc(t("volume_done"))}</button></div></div>`);
  const audio = new Audio(S.cfg.calibration_url);
  audio.loop = true;
  const play = document.getElementById("play");
  const go = document.getElementById("go");
  play.onclick = () => {
    if (audio.paused) { audio.play(); play.textContent = t("pause"); go.disabled = false; }
    else { audio.pause(); play.textContent = t("play"); }
  };
  go.onclick = () => {
    audio.pause();
    if (S.cfg.headphone_check.enabled) showHeadphone(0, []); else showInstructions();
  };
}

function showHeadphone(i, answers) {
  const trial = S.hpTrials[i];
  render(`<div class="panel"><h1>${esc(t("hp_title"))}</h1><p>${esc(t("hp_text"))}</p>
    <p class="muted">${esc(t("hp_trial", { n: i + 1, total: S.hpTrials.length }))}</p>
    <div class="actions"><button class="primary" id="play">${esc(t("play"))}</button></div>
    <p id="which" hidden>${esc(t("hp_which"))}</p>
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
          render(`<div class="panel"><h1>${esc(t("hp_fail_title"))}</h1><p>${esc(t("hp_fail_text"))}</p></div>`);
        }
      } catch (e) { showError(e); }
    };
  });
}

function showInstructions() {
  render(`<div class="panel"><h1>${esc(t("instr_title"))}</h1><p>${esc(t("instr_text"))}</p>
    <p class="example">${esc(t("instr_example"))}</p><p>${esc(t("instr_practice"))}</p>
    <div class="actions"><button class="primary" id="go">${esc(t("start"))}</button></div></div>`);
  document.getElementById("go").onclick = () => loadNext();
}

// ------------------------------------------------------------------ triads

async function loadNext() {
  render(`<p class="muted"><span class="spinner"></span>${esc(t("loading"))}</p>`);
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
  render(`<div class="panel"><h1>${esc(t("after_practice_title"))}</h1><p>${esc(t("after_practice_text"))}</p>
    <div class="actions"><button class="primary" id="go">${esc(t("start"))}</button></div></div>`);
  document.getElementById("go").onclick = () => showTriad(triad);
}

function showTriad(triad) {
  const shownAt = performance.now();
  const L = S.cfg.labels;
  const heading = triad.is_practice ? t("practice_label") : t("triad_label", { n: triad.n_done + 1 });
  setProgress(heading);
  render(`<h1>${esc(heading)}</h1>
    <p>${esc(S.cfg.prompt[S.lang])}</p>
    <div class="clips">
      ${triad.clips.map((c) => `
        <div class="clip" data-pos="${c.position}">
          <div class="letter">${c.position}</div>
          <button class="playbtn">${esc(t("play"))}</button>
          <div class="bar"><span></span></div>
          <div class="status"></div>
          <button class="selbtn" disabled>${esc(t("select"))}</button>
        </div>`).join("")}
    </div>
    <p class="note" id="listen">${esc(t("listen_first"))}</p>
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
      <button class="primary" id="submit" disabled>${esc(t("submit"))}</button>
      <span class="spacer"></span>
      <button id="nodiff" disabled>${esc(t("no_difference"))}</button>
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
      document.getElementById("sim-label").textContent = t("sim_label", { a, b });
      document.getElementById("con-label").textContent = t("con_label", { c: odd });
    }
    app.querySelectorAll(".clip").forEach((el) => {
      const sel = selected.includes(el.dataset.pos);
      el.classList.toggle("selected", sel);
      el.querySelector(".selbtn").textContent = sel ? t("selected") : t("select");
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
    btn.onclick = () => {
      if (audio.paused) { pauseOthers(pos); audio.play(); } else audio.pause();
    };
    audio.onplay = () => { btn.textContent = t("pause"); logEvent(pos, "play").catch(() => {}); };
    audio.onpause = () => {
      btn.textContent = t("play");
      if (!audio.ended) logEvent(pos, "pause").catch(() => {});
    };
    audio.ontimeupdate = () => {
      if (audio.duration) bar.style.width = `${(100 * audio.currentTime) / audio.duration}%`;
    };
    audio.onended = async () => {
      btn.textContent = t("play");
      try { await logEvent(pos, "ended"); } catch (e) { return showError(e); }
      heard.add(pos);
      status.textContent = `✓ ${t("heard")}`;
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
    render(`<p class="muted"><span class="spinner"></span>${esc(t("saving"))}</p>`);
    try {
      const res = await api("response", { method: "POST", body: {
        participant_id: S.pid, triad_id: triad.triad_id, rt_ms: Math.round(performance.now() - shownAt), ...payload,
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
      const m = document.getElementById("msg"); m.textContent = t("too_short"); m.hidden = false;
      return;
    }
    let embedding = null;
    if (S.cfg.similarity.backend === "embedding" && !triad.is_practice) {
      document.getElementById("submit").disabled = true;
      document.getElementById("submit").innerHTML = `<span class="spinner"></span>${esc(t("saving"))}`;
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
  render(`<div class="panel iv"><h1>${esc(t("iv_title"))}</h1>
    <p class="muted">${esc(t("iv_answer"))}:</p>
    <p class="answer">${esc(poles.sim)} ↔ ${esc(poles.con)}</p>
    ${qs ? `<p class="muted">${esc(t("iv_questions"))}</p><ul>${qs}</ul>` : ""}
    <label class="block" for="rs">${esc(t("iv_rev_sim"))}</label><input type="text" id="rs" maxlength="300">
    <label class="block" for="rc">${esc(t("iv_rev_con"))}</label><input type="text" id="rc" maxlength="300">
    <label class="block">${esc(t("iv_judgement"))}</label>
    <div class="radios">
      <label><input type="radio" name="j" value="new"> ${esc(t("iv_new"))}</label>
      ${earlier ? `<label><input type="radio" name="j" value="same"> ${esc(t("iv_same"))}</label>` : ""}
    </div>
    ${earlier ? `<select id="same" disabled>${earlier}</select>` : ""}
    <label class="block" for="note">${esc(t("iv_note"))}</label><textarea id="note" rows="2" maxlength="2000"></textarea>
    <p class="note" id="msg" hidden></p>
    <div class="actions"><button class="primary" id="go">${esc(t("iv_continue"))}</button></div></div>`);
  const sameSel = document.getElementById("same");
  app.querySelectorAll("input[name=j]").forEach((r) => {
    r.onchange = () => { if (sameSel) sameSel.disabled = r.value !== "same" || !r.checked; };
  });
  document.getElementById("go").onclick = async (ev) => {
    const j = app.querySelector("input[name=j]:checked");
    if (!j) { const m = document.getElementById("msg"); m.textContent = t("iv_choose"); m.hidden = false; return; }
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
  setProgress("");
  render(`<div class="panel"><h1>${esc(t("end_title"))}</h1><p>${esc(t("end_text"))}</p></div>`);
}

init();
