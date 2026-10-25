"""Analysis report of a database backup as one self-contained HTML page.

    python scripts/report.py path/to/dump-YYYYMMDD-HHMMSS [--groups ../invitation_links.md] [--out report.html]

The dump is made on the server with  php private/backup.php  and downloaded with SFTP.
--groups maps the access codes to group names: either the invitation list (invitation_links.md)
or private/access_codes.csv from the server. Without it, the codes themselves are shown.
Default output: report.html inside the dump folder.

The page contains participant data (answers, demographics, browser strings). Keep it private:
do not put it in the repository or on the web.

Sections
  1. Participation: sessions started / answered at least one comparison / finished, per group;
     all demographic answers and the listening situation.
  2. Plausibility checks (real person or script?): answer times, listening, browser, labels,
     free-text fields. Each check only marks a session for a closer look; it is not a verdict.
  3. Results per group: all constructs, which clips were put together, stop reasons.

Note: a session is created after the consent page, the demographic questions and the listening
situation page. People who opened the link but stopped before that are not in the database.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

csv.field_size_limit(1 << 30)
REPO = Path(__file__).resolve().parent.parent

# a clip of this length must be heard three times before an answer is possible
SCRIPT_UA = re.compile(r"python|curl|wget|headless|bot\b|crawler|spider|selenium|playwright|puppeteer|phantom|"
                       r"httpclient|go-http|java/|okhttp|node-fetch|axios", re.I)
GENERIC = {"good", "bad", "nice", "not nice", "ok", "okay", "yes", "no", "a", "b", "c", "x", "y", "gut", "schlecht",
           "schön", "nicht schön", "ja", "nein", "same", "different", "gleich", "anders", "test", "asdf"}


# ------------------------------------------------------------------ loading

def read_table(dump: Path, name: str) -> list[dict]:
    p = dump / f"{name}.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return [{k: (None if v == "\\N" else v) for k, v in row.items()} for row in csv.DictReader(f)]


def norm_code(c: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", (c or "").upper())


def read_groups(path: Path | None) -> dict[str, str]:
    """code (normalised) -> group name, from invitation_links.md or access_codes.csv."""
    if not path or not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    groups = {}
    if path.suffix == ".csv":
        for row in csv.DictReader(text.splitlines()):
            groups[norm_code(row.get("code"))] = (row.get("note") or "").strip() or row.get("code")
    else:   # markdown table: | Group | Code | link | ...
        for line in text.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 2 and re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}", cells[1]):
                name = re.sub(r"\s*\(.*?\)\s*", " ", cells[0]).strip()
                groups[norm_code(cells[1])] = name
    return groups


def ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None


def clip_names(elements: list[dict]) -> dict[int, str]:
    """element_id -> readable name, with the recording ID from stimuli/isd_clips.csv if available."""
    rec = {}
    p = REPO / "stimuli" / "isd_clips.csv"
    if p.exists():
        with open(p, encoding="utf-8") as f:
            rec = {r["file"]: r for r in csv.DictReader(f)}
    out = {}
    for e in elements:
        if e.get("is_practice") == "1":
            continue
        r = rec.get(e["filename"])
        label = e.get("category") or ""
        name = e["filename"].replace(".flac", "")
        if r:
            name += f" · {r['GroupID']} ({r['LocationID']})"
        if label:
            name += f" · {label}"
        out[int(e["element_id"])] = name
    return out


# ------------------------------------------------------------------ analysis

def gibberish(s: str | None) -> bool:
    if not s:
        return False
    t = s.strip().lower()
    if len(t) < 3:
        return True
    if t in GENERIC or re.fullmatch(r"(.)\1+", t):
        return True
    letters = re.sub(r"[^a-zäöüß]", "", t)
    return len(letters) >= 4 and not re.search(r"[aeiouyäöü]", letters)


def analyse(dump: Path, groups: dict[str, str]) -> dict:
    T = {n: read_table(dump, n) for n in ["elements", "participants", "triads", "constructs", "responses",
                                           "play_events", "interviewer_judgements"]}
    names = clip_names(T["elements"])
    triads = {t["triad_id"]: t for t in T["triads"]}
    constructs = {c["construct_id"]: c for c in T["constructs"]}
    resp_by_p = defaultdict(list)
    for r in T["responses"]:
        t = triads.get(r["triad_id"])
        if t:
            resp_by_p[t["participant_id"]].append(r)
    plays_by_triad = defaultdict(list)
    for e in T["play_events"]:
        plays_by_triad[e["triad_id"]].append(e)
    cfg_clip = {}
    for p in T["participants"]:
        try:
            cfg_clip[p["participant_id"]] = float(json.loads(p["config_snapshot"])["stimuli"]["clip_duration_s"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            cfg_clip[p["participant_id"]] = 15.0

    # label sets per participant, to find identical answers across sessions
    labelset = {}
    for p in T["participants"]:
        pid = p["participant_id"]
        labelset[pid] = {(c["similarity_pole"].strip().lower(), c["contrast_pole"].strip().lower())
                         for c in T["constructs"] if c["participant_id"] == pid and c.get("is_practice") != "1"}

    people, rows = [], []
    for p in T["participants"]:
        pid = p["participant_id"]
        code = norm_code(p.get("access_code"))
        group = groups.get(code, (p.get("access_code") or ("interviewer" if p["mode"] == "interviewer" else "no code")))
        rs = sorted(resp_by_p.get(pid, []), key=lambda r: r["submitted_at"])
        real = [r for r in rs if int(triads[r["triad_id"]]["order_index"]) >= 1]
        rts = [int(r["rt_ms"]) / 1000 for r in real if r.get("rt_ms")]
        clip_s = cfg_clip[pid]
        listen, replays = [], []
        for r in real:
            evs = sorted(plays_by_triad.get(r["triad_id"], []), key=lambda e: (e["logged_at"], int(e["at_ms"] or 0)))
            n_play = sum(1 for e in evs if e["event"] == "play")
            replays.append(max(0, n_play - 3))
            # server-side listening time: first play to last ended (server clock)
            st = [ts(e["logged_at"]) for e in evs if e["event"] == "play"]
            en = [ts(e["logged_at"]) for e in evs if e["event"] == "ended"]
            if st and en and all(st) and all(en):
                listen.append((max(en) - min(st)).total_seconds())
        started, finished = ts(p["started_at"]), ts(p.get("finished_at"))
        last = ts(rs[-1]["submitted_at"]) if rs else None
        end = finished or last
        dur_min = (end - started).total_seconds() / 60 if (started and end) else None
        cons = [constructs[r["construct_id"]] for r in real if r.get("construct_id") in constructs]
        n_new = sum(1 for c in cons if c.get("auto_is_new") == "1")
        hints = sum(1 for r in real if r.get("hints_shown") not in (None, "", "[]"))
        langs = {r.get("language") for r in rs if r.get("language")}

        # plausibility checks
        flags = []
        min_rt = 3 * clip_s
        fast = [x for x in rts if x < min_rt]
        if rts and statistics.median(rts) < min_rt + 15:
            flags.append(f"median answer time {statistics.median(rts):.0f} s (listening alone takes {min_rt:.0f} s)")
        elif fast:
            flags.append(f"{len(fast)} answer(s) faster than the three clips last ({min_rt:.0f} s)")
        if listen and statistics.median(listen) < 0.9 * clip_s:
            flags.append(f"server-side listening per comparison only {statistics.median(listen):.0f} s")
        ua = p.get("user_agent") or ""
        if not ua:
            flags.append("no browser string")
        elif SCRIPT_UA.search(ua):
            flags.append("browser string of a script or automated browser")
        texts = [t for c in cons for t in (c["similarity_pole"], c["contrast_pole"])]
        if texts:
            gen = sum(1 for t in texts if t.strip().lower() in GENERIC)
            if gen / len(texts) >= 0.5:
                flags.append(f"{gen} of {len(texts)} labels generic (good/bad, …)")
            pairs = Counter((c["similarity_pole"].strip().lower(), c["contrast_pole"].strip().lower()) for c in cons)
            rep = sum(n - 1 for n in pairs.values() if n > 1)
            if rep >= 2:
                flags.append(f"{rep} labels repeated word for word")
            mean_len = statistics.mean(len(t) for t in texts)
            if mean_len > 45:
                flags.append(f"very long labels (mean {mean_len:.0f} characters)")
            long_sentences = sum(1 for t in texts if len(t.split()) >= 8)
            if long_sentences >= 2 and mean_len <= 45:
                flags.append(f"{long_sentences} labels are whole sentences")
        # identical answers in another session; short common pairs ("calm - loud") are expected to
        # recur between real people, so only distinctive pairs (at least 25 characters together) count
        mine = {x for x in labelset.get(pid, set()) if len(x[0]) + len(x[1]) >= 25}
        if len(mine) >= 2:
            for other, theirs in labelset.items():
                same = mine & theirs
                if other != pid and len(same) >= 2:
                    flags.append(f"{len(same)} distinctive labels identical to session {short_id(other)}")
                    break
        if gibberish(p.get("headphones_model")):
            flags.append(f"device model looks random (“{p.get('headphones_model')}”)")
        if dur_min is not None and finished and len(real) >= 6 and dur_min < 6 * min_rt / 60 + 5:
            flags.append(f"finished {len(real)} comparisons in {dur_min:.0f} min")
        demo = [p.get(k) for k in ("age_group", "gender", "first_language", "sound_expertise", "residence")]
        if all(d in (None, "na") for d in demo):
            flags.append("no demographic answer given (weak sign)")

        people.append({
            "pid": pid, "short": short_id(pid), "group": group, "code": code,
            "mode": p["mode"], "language": p["language"], "lang_switch": len(langs) > 1,
            "started": p["started_at"], "finished": p.get("finished_at"), "stop": p.get("stop_reason"),
            "n_answers": len(real), "n_constructs": len(cons), "n_new": n_new,
            "n_nodiff": sum(1 for r in real if r.get("no_difference") == "1"),
            "hints": hints, "dur_min": round(dur_min, 1) if dur_min is not None else None,
            "rt_median": round(statistics.median(rts)) if rts else None,
            "rt_min": round(min(rts)) if rts else None,
            "listen_median": round(statistics.median(listen)) if listen else None,
            "replays": sum(replays),
            "device": p.get("listening_device"), "environment": p.get("listening_environment"),
            "model": p.get("headphones_model"), "hearing": p.get("hearing_problems"),
            "age": p.get("age_group"), "gender": p.get("gender"),
            "first_language": p.get("first_language"), "first_language_other": p.get("first_language_other"),
            "expertise": p.get("sound_expertise"), "residence": p.get("residence"),
            "ua": ua, "browser": browser(ua), "config": p.get("config_version"),
            "excluded": p.get("excluded") == "1", "flags": flags,
        })
        for r in real:
            t = triads[r["triad_id"]]
            shown = [int(t["pos_a"]), int(t["pos_b"]), int(t["pos_c"])]
            c = constructs.get(r.get("construct_id"))
            rows.append({
                "pid": pid, "short": people[-1]["short"], "group": group, "n": int(t["order_index"]),
                "shown": shown, "pair": [int(x) for x in (r.get("pair_a"), r.get("pair_b")) if x],
                "odd": int(r["odd_one"]) if r.get("odd_one") else None,
                "nodiff": r.get("no_difference") == "1",
                "sim": c["similarity_pole"] if c else None, "con": c["contrast_pole"] if c else None,
                "new": (c.get("auto_is_new") == "1") if c and c.get("auto_is_new") is not None else None,
                "score": round(float(c["similarity_score"]), 2) if c and c.get("similarity_score") else None,
                "rt": round(int(r["rt_ms"]) / 1000) if r.get("rt_ms") else None,
                "lang": r.get("language"),
            })
    return {"people": people, "rows": rows, "clips": {str(k): v for k, v in sorted(names.items())},
            "dump": dump.name, "made": datetime.now().strftime("%Y-%m-%d %H:%M")}


def short_id(pid: str) -> str:
    return pid[2:10] if pid.startswith("P-") else pid[:8]


def browser(ua: str) -> str:
    if not ua:
        return "–"
    for name, pat in [("Edge", r"Edg/"), ("Firefox", r"Firefox/"), ("Chrome", r"Chrome/"), ("Safari", r"Safari/")]:
        if re.search(pat, ua):
            os_ = "macOS" if "Mac OS" in ua else "Windows" if "Windows" in ua else "Linux" if "Linux" in ua else ""
            return f"{name} {os_}".strip()
    return ua[:30]


# ------------------------------------------------------------------ page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>RGT analysis</title>
<style>
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1f2421;--muted:#6b716c;--line:#e2e0d8;--accent:#2e6b4f;--accent-soft:#e3efe8;
--warn:#9a5b13;--warn-soft:#fbefdc;--bad:#a33b2f;--chip:#efeee9}
@media (prefers-color-scheme:dark){:root{--bg:#161917;--panel:#1f2321;--ink:#e6e8e5;--muted:#9aa29c;--line:#333a36;
--accent:#7cc4a0;--accent-soft:#22362c;--warn:#e3a95c;--warn-soft:#3a2c18;--bad:#ef8a7c;--chip:#2a2f2c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:20px 24px 8px}h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}h3{font-size:15px;margin:18px 0 8px}
.muted{color:var(--muted)}main{padding:0 24px 40px;max-width:1400px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:10px 0;overflow-x:auto}
.controls{position:sticky;top:0;z-index:5;background:var(--bg);padding:10px 24px;border-bottom:1px solid var(--line);display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.controls label{display:flex;gap:6px;align-items:center;cursor:pointer}
nav a{color:var(--accent);margin-right:14px;text-decoration:none}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.flagged td{background:var(--warn-soft)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.kpi b{display:block;font-size:24px}.kpi span{color:var(--muted);font-size:12px}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;margin:1px 2px;font-size:12px}
.flag{display:block;color:var(--warn);font-size:12px}.ok{color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:10px}
#demo{grid-template-columns:repeat(auto-fit,minmax(600px,1fr))}#demo td:first-child,#demo th{white-space:nowrap}#demo th.num{font-size:12px}
.bar{display:flex;align-items:center;gap:6px;margin:2px 0}.bar i{display:block;height:10px;background:var(--accent);border-radius:3px}
.heat td{text-align:center;min-width:42px;font-variant-numeric:tabular-nums}.heat th{font-size:11px}
details summary{cursor:pointer;color:var(--accent)}.small{font-size:12px}
.pole{font-weight:600}.sep{color:var(--muted);padding:0 4px}
</style></head><body>
<header><h1>Repertory grid study – analysis</h1>
<div class="muted">Backup <b id="dump"></b> · report made <span id="made"></span> · contains participant data: keep private.</div></header>
<div class="controls">
<label><input type="checkbox" id="f-test"> include the test group</label>
<label><input type="checkbox" id="f-flag" checked> include sessions marked in the checks</label>
<label><input type="checkbox" id="f-unf" checked> include unfinished sessions in the results</label>
<nav><a href="#s1">1 Participation</a><a href="#s2">2 Checks</a><a href="#s3">3 Results</a></nav>
</div>
<main>
<h2 id="s1">1 Participation</h2>
<p class="muted small">A session starts after consent, the demographic questions and the listening-situation page.
Opening the link without getting that far is not recorded. “Finished” = ended by the stopping rule, the maximum number of comparisons or the time limit.</p>
<div class="kpis" id="kpis"></div>
<div class="panel"><table id="t-groups"></table></div>
<h3>Demographics and listening situation</h3>
<div class="grid" id="demo"></div>
<details class="panel"><summary>All sessions (one row each)</summary><div style="overflow-x:auto"><table id="t-people"></table></div></details>

<h2 id="s2">2 Checks: real person or script?</h2>
<p class="muted small">Each check only marks a session for a closer look, it is not a verdict. The server already refuses answers before all three clips have been played to the end
(at least 90 % of their length by the server clock), limits new sessions per IP address and requires a group code.
Checks here: answer time compared with the listening time; listening time measured by the server; browser string;
generic, repeated, very long or sentence-like labels; labels identical to another session; a random-looking device model;
a very short session; no demographic answer (weak).</p>
<div class="panel"><table id="t-checks"></table></div>

<h2 id="s3">3 Results by group</h2>
<p class="muted small">Clips: <span id="cliplist"></span></p>
<div id="groups"></div>
</main>
<script>
const D = __DATA__;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const clip = (id) => D.clips[id] || ("clip " + id);
const shortClip = (id) => (D.clips[id] || String(id)).split(" · ")[0].replace("clip_", "c");
const midClip = (id) => { const p = (D.clips[id] || String(id)).split(" · "); return [shortClip(id), (p[1] || "").split(" ")[0], p[2] || ""].filter(Boolean).join(" "); };
$("#dump").textContent = D.dump; $("#made").textContent = D.made;
$("#cliplist").innerHTML = Object.entries(D.clips).map(([k, v]) => `<span class="chip">${esc(v)}</span>`).join(" ");

const LABELS = {
  age: ["Age", ["18-24","25-34","35-44","45-54","55-64","65+","na"]],
  gender: ["Gender", ["female","male","diverse","na"]],
  first_language: ["First language", ["de","en","other","na"]],
  expertise: ["Sound / music expertise", ["none","hobby","professional","na"]],
  residence: ["Lives in", ["city","town","rural","na"]],
  device: ["Listening device", ["headphones","speakers"]],
  environment: ["Surroundings", ["quiet","some_noise","noisy"]],
  language: ["Language at start", ["de","en"]],
  browser: ["Browser", null],
};
const isFinished = (p) => !!p.finished;

function people() {
  return D.people.filter((p) => ($("#f-test").checked || p.group !== "test") && ($("#f-flag").checked || !p.flags.length));
}
function groupsOf(ps) { return [...new Set(ps.map((p) => p.group))].sort(); }

function render() {
  const ps = people();
  const gs = groupsOf(ps);
  const fin = ps.filter(isFinished);
  const anyAns = ps.filter((p) => p.n_answers > 0);
  const med = (a) => { a = a.filter((x) => x != null).sort((x, y) => x - y); return a.length ? a[Math.floor((a.length - 1) / 2)] : null; };
  $("#kpis").innerHTML = [
    [ps.length, "sessions started"], [anyAns.length, "answered ≥ 1 comparison"], [fin.length, "finished"],
    [med(fin.map((p) => p.dur_min)) ?? "–", "median minutes (finished)"],
    [med(fin.map((p) => p.n_answers)) ?? "–", "median comparisons (finished)"],
    [ps.filter((p) => p.flags.length).length, "marked in the checks"],
  ].map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");

  // per group table
  let h = `<tr><th>Group</th><th class="num">started</th><th class="num">≥1 answer</th><th class="num">finished</th>
    <th>stop reasons</th><th class="num">median min</th><th class="num">median comparisons</th><th class="num">constructs</th><th class="num">marked</th></tr>`;
  for (const g of gs) {
    const gp = ps.filter((p) => p.group === g), gf = gp.filter(isFinished);
    const stops = Object.entries(gf.reduce((a, p) => (a[p.stop] = (a[p.stop] || 0) + 1, a), {})).map(([k, v]) => `<span class="chip">${esc(k)} ${v}</span>`).join("");
    h += `<tr><td><b>${esc(g)}</b></td><td class="num">${gp.length}</td><td class="num">${gp.filter((p) => p.n_answers).length}</td>
      <td class="num">${gf.length}</td><td>${stops || "–"}</td><td class="num">${med(gf.map((p) => p.dur_min)) ?? "–"}</td>
      <td class="num">${med(gf.map((p) => p.n_answers)) ?? "–"}</td><td class="num">${gp.reduce((a, p) => a + p.n_constructs, 0)}</td>
      <td class="num">${gp.filter((p) => p.flags.length).length}</td></tr>`;
  }
  $("#t-groups").innerHTML = h;

  // demographics: one small table per variable, columns = groups
  $("#demo").innerHTML = Object.entries(LABELS).map(([k, [title, order]]) => {
    const vals = order || [...new Set(ps.map((p) => p[k] || "–"))].sort();
    let t = `<table><tr><th>${title}</th>${gs.map((g) => `<th class="num">${esc(g)}</th>`).join("")}<th class="num">all</th></tr>`;
    for (const v of vals) {
      const n = (arr) => arr.filter((p) => (p[k] || "–") === v).length;
      if (!n(ps)) continue;
      t += `<tr><td>${esc(v)}</td>${gs.map((g) => `<td class="num">${n(ps.filter((p) => p.group === g)) || ""}</td>`).join("")}<td class="num"><b>${n(ps)}</b></td></tr>`;
    }
    return `<div class="panel">${t}</table></div>`;
  }).join("") + `<div class="panel"><table><tr><th>Free text</th><th>Session</th></tr>${
    ps.filter((p) => p.model || p.first_language_other || p.hearing).map((p) => `<tr><td>${[
      p.model && `device: ${esc(p.model)}`, p.first_language_other && `language: ${esc(p.first_language_other)}`,
      p.hearing && `hearing: ${esc(p.hearing)}`].filter(Boolean).join("<br>")}</td><td>${esc(p.short)} <span class="muted">${esc(p.group)}</span></td></tr>`).join("")}</table></div>`;

  // all sessions
  const cols = [["short","session"],["group","group"],["language","lang"],["started","started (UTC)"],["finished","finished"],["stop","stop"],
    ["n_answers","comparisons"],["n_constructs","constructs"],["n_new","new"],["n_nodiff","no difference"],["hints","ideas opened"],["dur_min","minutes"],
    ["age","age"],["gender","gender"],["first_language","first lang."],["expertise","expertise"],["residence","lives in"],
    ["device","device"],["model","model"],["environment","surroundings"],["browser","browser"],["config","config"]];
  $("#t-people").innerHTML = `<tr>${cols.map(([, l]) => `<th>${l}</th>`).join("")}</tr>` + ps.map((p) =>
    `<tr class="${p.flags.length ? "flagged" : ""}">${cols.map(([k]) => `<td>${esc(k === "started" || k === "finished" ? (p[k] || "").slice(0, 16).replace("T", " ") : p[k] ?? "")}</td>`).join("")}</tr>`).join("");

  // checks
  $("#t-checks").innerHTML = `<tr><th>Session</th><th>Group</th><th class="num">compar.</th><th class="num">median answer s</th><th class="num">fastest s</th>
    <th class="num">listening s (server)</th><th class="num">replays</th><th class="num">ideas</th><th>browser</th><th>result</th></tr>` +
    ps.slice().sort((a, b) => b.flags.length - a.flags.length).map((p) => `<tr class="${p.flags.length ? "flagged" : ""}">
    <td>${esc(p.short)}</td><td>${esc(p.group)}</td><td class="num">${p.n_answers}</td><td class="num">${p.rt_median ?? "–"}</td>
    <td class="num">${p.rt_min ?? "–"}</td><td class="num">${p.listen_median ?? "–"}</td><td class="num">${p.replays}</td><td class="num">${p.hints}</td>
    <td title="${esc(p.ua)}">${esc(p.browser)}</td>
    <td>${p.flags.length ? p.flags.map((f) => `<span class="flag">⚑ ${esc(f)}</span>`).join("") : '<span class="ok">no signs</span>'}</td></tr>`).join("");

  // results per group
  const ids = new Set(ps.filter((p) => $("#f-unf").checked || isFinished(p)).map((p) => p.pid));
  const rows = D.rows.filter((r) => ids.has(r.pid));
  const clipIds = Object.keys(D.clips).map(Number);
  $("#groups").innerHTML = ["all groups", ...gs].map((g) => {
    const gr = g === "all groups" ? rows : rows.filter((r) => r.group === g);
    if (!gr.length) return "";
    // how often two clips were put together, relative to how often both were shown
    const together = {}, shown = {};
    for (const r of gr) {
      for (let i = 0; i < 3; i++) for (let j = i + 1; j < 3; j++) { const k = [r.shown[i], r.shown[j]].sort((a, b) => a - b).join("-"); shown[k] = (shown[k] || 0) + 1; }
      if (r.pair.length === 2) { const k = r.pair.slice().sort((a, b) => a - b).join("-"); together[k] = (together[k] || 0) + 1; }
    }
    let heat = `<table class="heat"><tr><th></th>${clipIds.map((c) => `<th title="${esc(clip(c))}">${esc(shortClip(c))}</th>`).join("")}</tr>`;
    for (const a of clipIds) {
      heat += `<tr><th title="${esc(clip(a))}">${esc(shortClip(a))}</th>`;
      for (const b of clipIds) {
        if (a === b) { heat += "<td></td>"; continue; }
        const k = [a, b].sort((x, y) => x - y).join("-"), s = shown[k] || 0, t = together[k] || 0;
        const share = s ? t / s : null;
        heat += share == null ? `<td class="muted">·</td>` :
          `<td style="background:color-mix(in srgb, var(--accent) ${Math.round(share * 85)}%, transparent)" title="${t} of ${s}">${Math.round(share * 100)}</td>`;
      }
      heat += "</tr>";
    }
    heat += "</table>";
    const cons = gr.filter((r) => r.sim);
    const odd = {}; for (const r of gr) if (r.odd) odd[r.odd] = (odd[r.odd] || 0) + 1;
    const maxOdd = Math.max(1, ...Object.values(odd));
    const words = {};
    for (const r of cons) for (const t of [r.sim, r.con]) { const w = t.trim().toLowerCase(); words[w] = (words[w] || 0) + 1; }
    const topWords = Object.entries(words).sort((a, b) => b[1] - a[1]).slice(0, 30);
    const table = `<table><tr><th>session</th><th class="num">#</th><th>clips shown</th><th>put together</th><th>different</th>
      <th>what the two have in common</th><th></th><th>how the third differs</th><th>new?</th><th class="num">sim.</th><th class="num">s</th></tr>` +
      gr.slice().sort((a, b) => a.pid.localeCompare(b.pid) || a.n - b.n).map((r) => `<tr><td>${esc(r.short)}${g === "all groups" ? ` <span class="muted">${esc(r.group)}</span>` : ""}</td>
      <td class="num">${r.n}</td><td class="small">${r.shown.map(shortClip).join(", ")}</td>
      <td class="small">${r.pair.map(shortClip).join(" + ") || "–"}</td><td class="small">${r.odd ? shortClip(r.odd) : "–"}</td>
      ${r.nodiff ? `<td colspan="3" class="muted">no new difference</td>` : `<td class="pole">${esc(r.sim)}</td><td class="sep">↔</td><td class="pole">${esc(r.con)}</td>`}
      <td>${r.new == null ? "" : r.new ? "new" : '<span class="muted">repeat</span>'}</td><td class="num">${r.score ?? ""}</td><td class="num">${r.rt ?? ""}</td></tr>`).join("") + "</table>";
    return `<div class="panel"><h3>${esc(g)} <span class="muted small">${new Set(gr.map((r) => r.pid)).size} sessions · ${gr.length} comparisons · ${cons.length} constructs · ${cons.filter((r) => r.new).length} judged new</span></h3>
      <div class="grid"><div><h3 class="small muted">How often two clips were put together (% of the comparisons in which both were shown; · = never shown together; hover for counts)</h3>${heat}</div>
      <div><h3 class="small muted">How often each clip was the different one</h3>${clipIds.map((c) => `<div class="bar"><span class="small" style="width:210px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(clip(c))}">${esc(midClip(c))}</span><i style="width:${(odd[c] || 0) / maxOdd * 140}px"></i><span class="small">${odd[c] || 0}</span></div>`).join("")}
      <h3 class="small muted">Most frequent labels</h3>${topWords.map(([w, n]) => `<span class="chip">${esc(w)} ${n > 1 ? "×" + n : ""}</span>`).join("") || "–"}</div></div>
      <details><summary>All ${gr.length} answers</summary>${table}</details></div>`;
  }).join("");
}
for (const id of ["#f-test", "#f-flag", "#f-unf"]) $(id).addEventListener("change", render);
render();
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump", type=Path)
    ap.add_argument("--groups", type=Path, default=REPO.parent / "invitation_links.md",
                    help="invitation_links.md or access_codes.csv (default: ../invitation_links.md)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    if not (args.dump / "participants.csv").exists():
        sys.exit(f"{args.dump}: not a backup folder (participants.csv missing)")
    groups = read_groups(args.groups)
    if not groups:
        print(f"note: no group names found in {args.groups}; the access codes are shown instead")
    data = analyse(args.dump, groups)
    out = args.out or args.dump / "report.html"
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(PAGE.replace("__DATA__", payload), encoding="utf-8")
    n = len(data["people"])
    print(f"written: {out} ({n} sessions, {sum(1 for p in data['people'] if p['finished'])} finished, "
          f"{sum(1 for p in data['people'] if p['flags'])} marked in the checks)")


if __name__ == "__main__":
    main()
