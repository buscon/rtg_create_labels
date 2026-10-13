"""Shortlists of European recordings from the International Soundscape Database (ISD),
with the PAQ ratings given on site, and an interactive listening page.

    python stimuli/isd_shortlist.py --mode paq      [--per-direction 3] [--shrink 1] [--no-download]
    python stimuli/isd_shortlist.py --mode sources  [--per-category 8] [--max-per-location 2]

ISD v1.0 (Mitchell et al.), Zenodo doi:10.5281/zenodo.10672568, CC BY 4.0.
Each ISD recording (named after its GroupID, e.g. RF104) is a binaural recording made
while one or more people filled in the ISO 12913-2 questionnaire at the same spot.

Common steps
  1. downloads the survey data (ISD v1.0 Data.csv, 2 MB) to stimuli/raw/isd/;
  2. keeps the European locations (London, Venice, Granada, Groningen) and recordings of
     at least 30 s, and computes per recording the mean ISO pleasantness P and
     eventfulness E (ISO/TS 12913-3, -1..1) and the mean source ratings (traffic, other
     noise, human, natural; 1-5). Most recordings were rated by one person, so P and E
     are also given "shrunk" towards the mean of the location (P_s, E_s): the location
     mean counts as --shrink extra respondents. Selection uses P_s / E_s.
  3. labels each recording natural / mixed / urban from its source ratings:
       natural: natural >= 4, traffic <= 2, other noise <= 2, at a location where natural
                sounds are rated >= 3 on average
       urban:   traffic >= 3 or other noise >= 4, with natural <= 2.5, at a location
                where traffic is rated >= 2.5 on average
       mixed:   natural >= 3 and traffic, human or other noise >= 3

Selection
  --mode paq (default): for each of the 8 PAQ directions (pleasant, vibrant, eventful,
     chaotic, annoying, monotonous, uneventful, calm) the --per-direction recordings that
     lie furthest in that direction (projection of (P_s, E_s) on the direction), each from
     a different location, no recording used twice; recordings rated by 2+ people get a
     small bonus (+0.05).
  --mode sources: per category, recordings that differ as much as possible in P_s, E_s,
     LAeq and level spread (farthest-point sampling), at most --max-per-location per location.

Output (stimuli/work/isd/): shortlist_<mode>.html (interactive P/E map: hover a point to
hear it; table with players), shortlist_<mode>.csv (with empty keep / note columns),
all_recordings.csv, previews/*.mp3 with ONE common gain (real level differences kept).
Only the shortlisted recordings are downloaded (about 12 MB each).

Caveat: the ratings were given on site. They describe the place as experienced (view,
weather, reason for being there), not the recording alone.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, WORK, leq_dbfs  # noqa: E402
from remotezip import RemoteZip  # noqa: E402

BASE = "https://zenodo.org/records/10672568/files/"
ZIPS = ["WAV_London_1.zip", "WAV_London_2.zip", "WAV_London_3.zip", "WAV_London_4.zip",
        "WAV_Venice_1.zip", "WAV_Granada_1.zip", "WAV_Groningen_1.zip"]
CITIES = {
    "London": ["CamdenTown", "EustonTap", "MarchmontGarden", "PancrasLock", "RegentsParkFields",
               "RegentsParkJapan", "RussellSq", "StPaulsCross", "StPaulsRow", "TateModern", "TorringtonSq"],
    "Venice": ["SanMarco", "MonumentoGaribaldi"],
    "Granada": ["CarloV", "CampoPrincipe", "MiradorSanNicolas", "PlazaBibRambla"],
    "Groningen": ["Noorderplantsoen"],
}
DIRECTIONS = [("pleasant", 0), ("vibrant", 45), ("eventful", 90), ("chaotic", 135),
              ("annoying", 180), ("monotonous", 225), ("uneventful", 270), ("calm", 315)]
CREDIT = ("International Soundscape Database v1.0 (Mitchell, Oberman, Aletta, Erfanian, Kachlicka, "
          "Lionello, Kang et al.), Zenodo, doi:10.5281/zenodo.10672568, CC BY 4.0")
P_REF = 2e-5
SHRINK = 1
MIN_LENGTH = 30          # s; shorter recordings cannot give a 30-s clip
LEVEL_TOLERANCE = 3      # dB between the file and the published LAeq before the file is corrected
OUT = WORK / "isd"
RAWDIR = RAW / "isd"


def fetch(name: str, dest: Path, retries: int = 8) -> Path:
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = BASE + urllib.request.quote(name) + "?download=1"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            dest.write_bytes(data)
            return dest
        except Exception as exc:
            wait = min(30 * (attempt + 1), 300)
            print(f"  {name}: {exc}, retrying in {wait}s")
            time.sleep(wait)
    sys.exit(f"could not download {name}; get it manually from {url} and put it at {dest}")



def recordings_table(shrink: float = SHRINK) -> pd.DataFrame:
    d = pd.read_csv(fetch("ISD v1.0 Data.csv", RAWDIR / "ISD_v1.0_Data.csv"), low_memory=False)
    city = {loc: c for c, locs in CITIES.items() for loc in locs}
    d = d[d.LocationID.isin(city)].copy()
    d["city"] = d.LocationID.map(city)
    c = math.cos(math.pi / 4)
    norm = 4 + math.sqrt(32)
    d["P"] = ((d.pleasant - d.annoying) + c * (d.calm - d.chaotic) + c * (d.vibrant - d.monotonous)) / norm
    d["E"] = ((d.eventful - d.uneventful) + c * (d.chaotic - d.calm) + c * (d.vibrant - d.monotonous)) / norm
    loc = d.groupby("LocationID").agg(loc_P=("P", "mean"), loc_E=("E", "mean"), loc_n=("P", "count"),
                                      loc_natural=("ssi04", "mean"), loc_traffic=("ssi01", "mean"))
    g = d.groupby(["city", "LocationID", "GroupID"]).agg(
        n=("P", "count"), P=("P", "mean"), E=("E", "mean"),
        traffic=("ssi01", "mean"), other=("ssi02", "mean"), human=("ssi03", "mean"), natural=("ssi04", "mean"),
        LAeq=("LAeq_L(A)", "mean"), LA10=("LAeq_L10(A)", "mean"), LA90=("LAeq_L90(A)", "mean"),
        date=("start_time", "first"), length=("RecordingLength", "max")).reset_index()
    g = g[(g.n > 0) & (g.length.isna() | (g.length >= MIN_LENGTH))].join(loc, on="LocationID")
    g["LA10_LA90"] = g.LA10 - g.LA90
    # most recordings were rated by one person: shrink towards the location mean
    # (weight of the location mean = shrink respondents) for a steadier estimate
    g["P_s"] = (g.n * g.P + shrink * g.loc_P) / (g.n + shrink)
    g["E_s"] = (g.n * g.E + shrink * g.loc_E) / (g.n + shrink)
    return g


def category(r) -> str:
    if r.natural >= 4 and r.traffic <= 2 and r.other <= 2 and r.loc_natural >= 3:
        return "natural"
    if (r.traffic >= 4 or r.other >= 4 or r.traffic >= 3) and r.natural <= 2.5 and r.loc_traffic >= 2.5:
        return "urban"
    if r.natural >= 3 and max(r.traffic, r.human, r.other) >= 3:
        return "mixed"
    return ""


def zip_members() -> dict[str, tuple[str, str]]:
    """GroupID -> (zip, member) for every WAV in the European archives (indexes are cached)."""
    members = {}
    for z in ZIPS:
        rz = RemoteZip(BASE + z, retries=10, cache=RAWDIR / f".index_{z}.json")
        for name in rz.names():
            if name.lower().endswith(".wav") and "__MACOSX" not in name:
                gid = name.rsplit("/", 1)[1].split(".")[0]
                members.setdefault(gid, (z, name))
    return members


def pick(rows: pd.DataFrame, k: int, max_loc: int) -> pd.DataFrame:
    """Farthest-point sampling on standardised P, E, LAeq, LA10-LA90, with a cap per location.
    Recordings rated by 2+ people are tried first."""
    feats = ["P_s", "E_s", "LAeq", "LA10_LA90"]
    z = ((rows[feats] - rows[feats].mean()) / rows[feats].std(ddof=0).replace(0, 1)).fillna(0).to_numpy()
    multi = np.flatnonzero(rows.n.to_numpy() >= 2)
    start_pool = multi if len(multi) else np.arange(len(rows))   # start with a recording rated by 2+ people
    chosen: list[int] = []
    per_loc: dict[str, int] = {}
    locs = rows.LocationID.to_numpy()
    centre = np.median(z, axis=0)
    first = min(start_pool, key=lambda i: np.linalg.norm(z[i] - centre))
    chosen.append(int(first))
    per_loc[locs[first]] = 1
    while len(chosen) < min(k, len(rows)):
        best, best_d = None, -1.0
        for i in range(len(rows)):
            if i in chosen or per_loc.get(locs[i], 0) >= max_loc:
                continue
            dist = min(np.linalg.norm(z[i] - z[j]) for j in chosen) + (0.25 if rows.n.iloc[i] >= 2 else 0)
            if dist > best_d:
                best, best_d = i, dist
        if best is None:
            break
        chosen.append(best)
        per_loc[locs[best]] = per_loc.get(locs[best], 0) + 1
    return rows.iloc[chosen]


def pick_paq(g: pd.DataFrame, per_direction: int, directions: list[str] | None = None,
             max_loc: int = 1, exclude: set[str] | None = None) -> pd.DataFrame:
    used: set[str] = set(exclude or ())
    rows = []
    for name, deg in DIRECTIONS:
        if directions and name not in directions:
            continue
        a = math.radians(deg)
        proj = g.P_s * math.cos(a) + g.E_s * math.sin(a)
        score = proj + 0.05 * (g.n >= 2)
        locs: dict[str, int] = {}
        picked = 0
        for i in score.sort_values(ascending=False).index:
            r = g.loc[i]
            if r.GroupID in used or locs.get(r.LocationID, 0) >= max_loc or proj[i] <= 0:
                continue
            rows.append({**r.to_dict(), "direction": name, "projection": proj[i]})
            used.add(r.GroupID)
            locs[r.LocationID] = locs.get(r.LocationID, 0) + 1
            picked += 1
            if picked == per_direction:
                break
    return pd.DataFrame(rows)


def download_and_preview(short: pd.DataFrame, members: dict) -> pd.DataFrame:
    zips: dict[str, RemoteZip] = {}
    for i, r in enumerate(short.itertuples(), 1):
        z, member = members[r.GroupID]
        dest = RAWDIR / r.LocationID / f"{r.GroupID}.wav"
        if not dest.exists():
            if z not in zips:
                zips[z] = RemoteZip(BASE + z, retries=10, cache=RAWDIR / f".index_{z}.json")
            zips[z].extract(member, dest, label=f"{r.GroupID}.wav ({i}/{len(short)})")
    # The London, Venice and Granada files are in pascal and match the published LAeq.
    # Others (Groningen) use a different scale: those are corrected to the published LAeq.
    # The common gain is fixed (not taken from the loudest file), so previews made in
    # different runs and modes stay comparable.
    gain = 10 ** (PREVIEW_GAIN_DB / 20)
    ext = ".mp3" if "MP3" in sf.available_formats() else ".wav"
    info = {}
    for r in short.itertuples():
        x, fs = sf.read(RAWDIR / r.LocationID / f"{r.GroupID}.wav", always_2d=True)
        lf = round(leq_dbfs(x[:, 0], fs) + 20 * math.log10(1 / P_REF), 1)
        diff = r.LAeq - lf
        note = ""
        if abs(diff) > LEVEL_TOLERANCE:
            x = x * 10 ** (diff / 20)
            note = f"file {lf:.1f} dB, corrected by {diff:+.1f} dB"
        y = x * gain
        if np.abs(y).max() > 1:
            note = (note + "; " if note else "") + "preview clipped"
        prev = OUT / "previews" / f"{r.GroupID}{ext}"
        if not prev.exists() or note:
            kw = {"format": "MP3", "subtype": "MPEG_LAYER_III"} if ext == ".mp3" else {"subtype": "PCM_16"}
            sf.write(prev, np.clip(y, -1, 1).astype(np.float32), fs, **kw)
        info[r.GroupID] = (lf, note, round(len(x) / fs, 1), f"previews/{r.GroupID}{ext}")
    short = short.copy()
    short["laeq_file"] = short.GroupID.map(lambda k: info[k][0])
    short["level_note"] = short.GroupID.map(lambda k: info[k][1])
    short["duration_s"] = short.GroupID.map(lambda k: info[k][2])
    short["preview"] = short.GroupID.map(lambda k: info[k][3])
    return short


PREVIEW_GAIN_DB = -13.0   # re 1 Pa: an 87 dB(A) street peaks just below full scale


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["paq", "sources"], default="paq")
    ap.add_argument("--per-direction", type=int, default=3, help="paq mode: recordings per PAQ direction")
    ap.add_argument("--per-category", type=int, default=8, help="sources mode: recordings per category")
    ap.add_argument("--max-per-location", type=int, default=None,
                    help="recordings per location (per direction in paq mode; default paq 1, sources 2)")
    ap.add_argument("--directions", nargs="*", default=None, metavar="DIR",
                    help="paq mode: only these directions, e.g. chaotic monotonous")
    ap.add_argument("--chosen", nargs="*", default=[], metavar="ID=LABEL",
                    help="recordings already chosen (e.g. CV41=calm): shown on the map and excluded from the search")
    ap.add_argument("--exclude-locations", nargs="*", default=[], metavar="LOC",
                    help="leave out these LocationIDs in the search (e.g. those already in the selection)")
    ap.add_argument("--name", default=None, help="output name suffix (default: the mode)")
    ap.add_argument("--shrink", type=float, default=SHRINK,
                    help="weight of the location mean in P_s/E_s, in respondents (0 = off; default 1)")
    ap.add_argument("--no-download", action="store_true", help="only write the tables, no audio")
    args = ap.parse_args()
    name = args.name or args.mode

    g = recordings_table(args.shrink)
    print("reading the zip indexes (cached after the first run) ...")
    members = zip_members()
    g = g[g.GroupID.isin(members)].copy()
    g["category"] = g.apply(category, axis=1)

    if args.mode == "paq":
        chosen = dict(c.split("=", 1) if "=" in c else (c, "") for c in args.chosen)
        unknown = [c for c in chosen if c not in set(g.GroupID)]
        if unknown:
            print(f"not found among the usable European recordings: {', '.join(unknown)}")
        pool = g[~g.LocationID.isin(args.exclude_locations)]
        short = pick_paq(pool, args.per_direction, args.directions, args.max_per_location or 1, set(chosen))
        mine = g[g.GroupID.isin(chosen)].copy()
        mine["direction"] = ["chosen: " + (chosen[k] or "-") for k in mine.GroupID]
        mine["projection"] = np.nan
        short = pd.concat([mine, short], ignore_index=True)
        group_col = "direction"
    else:
        parts = [pick(g[g.category == c].reset_index(drop=True), args.per_category, args.max_per_location or 2)
                 .sort_values("LAeq").assign(direction="") for c in ["natural", "mixed", "urban"]]
        short = pd.concat(parts, ignore_index=True)
        group_col = "category"

    (OUT / "previews").mkdir(parents=True, exist_ok=True)
    if args.no_download:
        for c in ("laeq_file", "level_note", "duration_s", "preview"):
            short[c] = ""
    else:
        short = download_and_preview(short, members)

    short["zip"] = short.GroupID.map(lambda gid: members[gid][0])
    short["keep"], short["note"] = "", ""
    cols = ["direction", "category", "GroupID", "city", "LocationID", "date", "n", "P", "E", "P_s", "E_s",
            "loc_P", "loc_E", "loc_n", "natural", "traffic", "human", "other", "LAeq", "LA10_LA90",
            "laeq_file", "level_note", "duration_s", "zip", "keep", "note", "preview"]
    out = short[cols].round(2)
    out.to_csv(OUT / f"shortlist_{name}.csv", index=False)
    g[["category", "GroupID", "city", "LocationID", "n", "P", "E", "P_s", "E_s", "natural", "traffic",
       "human", "other", "LAeq", "LA10_LA90"]].round(2).to_csv(OUT / "all_recordings.csv", index=False)
    write_html(OUT / f"shortlist_{name}.html", out, g, args, group_col)
    (OUT / "ATTRIBUTION.md").write_text(f"- {CREDIT}\n", encoding="utf-8")
    print(f"\n{len(out)} recordings -> {OUT / f'shortlist_{name}.html'}")


def write_html(path: Path, short: pd.DataFrame, allrec: pd.DataFrame, args, group_col: str) -> None:
    sel = short.to_dict("records")
    pts = [{"id": r.GroupID, "P": round(r.P_s, 3), "E": round(r.E_s, 3), "cat": r.category or "",
            "loc": r.LocationID} for r in allrec.itertuples()]
    if args.mode == "paq":
        dirs = ", ".join(args.directions) if args.directions else "each of the 8 PAQ directions"
        intro = (f"For {dirs}: the {args.per_direction} recordings furthest in that direction "
                 f"(at most {args.max_per_location or 1} per location)."
                 + (" Red rings: recordings already chosen." if args.chosen else ""))
    else:
        intro = "Per source category (natural / mixed / urban), recordings that differ as much as possible."
    cols = ["GroupID", "city", "LocationID", "category", "n", "P", "E", "P_s", "E_s", "natural", "traffic",
            "human", "other", "LAeq", "LA10_LA90", "duration_s", "level_note"]
    head = "<th>preview</th>" + "".join(f"<th>{c}</th>" for c in cols)
    body, current = [], None
    for r in sel:
        grp = r[group_col] or "-"
        if grp != current:
            if current is not None:
                body.append("</tbody></table>")
            body.append(f"<h2>{html.escape(grp)}</h2><table><thead><tr>{head}</tr></thead><tbody>")
            current = grp
        audio = (f"<audio controls preload='none' src='{html.escape(r['preview'])}'></audio>"
                 if r["preview"] else "")
        cells = "".join(f"<td>{html.escape('' if pd.isna(r[c]) else str(r[c]))}</td>" for c in cols)
        body.append(f"<tr data-id='{r['GroupID']}'><td>{audio}</td>{cells}</tr>")
    body.append("</tbody></table>")
    data = json.dumps({"all": pts, "sel": [{"id": r["GroupID"], "P": r["P_s"], "E": r["E_s"],
                                            "cat": r["category"] or "", "dir": r["direction"],
                                            "loc": r["LocationID"], "city": r["city"], "n": r["n"],
                                            "LAeq": r["LAeq"], "src": r["preview"],
                                            "nat": r["natural"], "tra": r["traffic"], "hum": r["human"]}
                                           for r in sel],
                       "dirs": DIRECTIONS})
    path.write_text(PAGE.replace("__TITLE__", f"ISD shortlist ({args.mode})")
                    .replace("__INTRO__", html.escape(intro))
                    .replace("__N__", str(len(sel)))
                    .replace("__SHRINK__", str(args.shrink))
                    .replace("__TABLES__", "".join(body))
                    .replace("__CREDIT__", html.escape(CREDIT))
                    .replace("__DATA__", data), encoding="utf-8")


PAGE = r"""<!doctype html><meta charset="utf-8"><title>__TITLE__</title>
<style>
body{font:14px system-ui,sans-serif;margin:1.5rem;max-width:1500px;color:#222}
table{border-collapse:collapse;margin-bottom:1rem}td,th{border-bottom:1px solid #ddd;padding:.3rem .5rem;text-align:left;white-space:nowrap}
th{background:#f3f3f0}audio{height:28px}h2{margin-top:1.6rem;text-transform:capitalize}p{max-width:72rem}
#wrap{display:flex;gap:1.5rem;align-items:flex-start;flex-wrap:wrap}
#map{border:1px solid #ddd;background:#fff;cursor:default;user-select:none}
#info{min-width:18rem;max-width:24rem;padding:.8rem 1rem;border:1px solid #ddd;background:#fafaf7}
#info b{font-size:1.1em}.hint{color:#777;font-size:.9em}
.sel{cursor:pointer}.sel text{pointer-events:none}.sel:hover circle{stroke-width:2.5}
tr.hl{background:#fff4c2}#gate{background:#fff4c2;padding:.5rem .8rem;display:inline-block;border-radius:4px}
label{margin-right:1rem}
</style>
<h1>__TITLE__: __N__ recordings</h1>
<p>__INTRO__ Position = ISO pleasantness / eventfulness of the recording from the on-site ratings, pulled towards
the location mean (location counts as __SHRINK__ extra respondent). <b>Previews keep the real level differences</b>
(one fixed gain): set the volume once and leave it. Grey dots: all other European ISD recordings (no audio).</p>
<p id="gate">Click anywhere on the page once to allow sound (browser rule), then hover over a coloured point.</p>
<div id="wrap">
<svg id="map" width="600" height="620" viewBox="0 0 600 620"></svg>
<div id="info"><b>Hover over a coloured point</b><p class="hint">Click a point to keep it playing after you leave it;
click again (or another point) to stop.</p>
<p><label><input type="checkbox" id="stopleave" checked> stop when the mouse leaves the point</label></p>
<div id="details"></div></div>
</div>
__TABLES__
<p>Source: __CREDIT__</p>
<script>
const D = __DATA__;
const svg = document.getElementById('map'), NS = 'http://www.w3.org/2000/svg';
const S = 600, M = 62, R = (S - 2*M) / 2, C = S / 2;
const X = p => C + p * R, Y = e => C - e * R;
const COL = {natural:'#2e7d32', mixed:'#c08a00', urban:'#3f51b5', '':'#888'};
function el(tag, attrs, parent){const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);(parent||svg).appendChild(e);return e;}
el('circle',{cx:C,cy:C,r:R,fill:'none',stroke:'#ddd'});
el('line',{x1:M,y1:C,x2:S-M,y2:C,stroke:'#bbb'}); el('line',{x1:C,y1:M,x2:C,y2:S-M,stroke:'#bbb'});
el('line',{x1:C-R*.707,y1:C-R*.707,x2:C+R*.707,y2:C+R*.707,stroke:'#eee'});
el('line',{x1:C-R*.707,y1:C+R*.707,x2:C+R*.707,y2:C-R*.707,stroke:'#eee'});
for (const [name,deg] of D.dirs){const a=deg*Math.PI/180, t=el('text',{x:C+Math.cos(a)*(R+18),y:C-Math.sin(a)*(R+18)+4,
  'text-anchor':'middle','font-size':12,fill:'#555'}); t.textContent=name;}
const selIds = new Set(D.sel.map(s=>s.id));
for (const p of D.all) if(!selIds.has(p.id)) el('circle',{cx:X(p.P),cy:Y(p.E),r:2.2,fill:'#999',opacity:.35});
const audio = new Audio(); audio.preload='none';
let unlocked=false, pinned=null, current=null;
document.addEventListener('click',()=>{ if(!unlocked){unlocked=true; document.getElementById('gate').style.display='none';}}, {capture:true});
function show(s){
  document.getElementById('details').innerHTML =
   `<p><b>${s.id}</b> &middot; ${s.loc} (${s.city})</p>
    <p>${s.dir?('direction: <b>'+s.dir+'</b><br>'):''}category: ${s.cat||'-'}<br>
    P ${(+s.P).toFixed(2)}, E ${(+s.E).toFixed(2)} &middot; ${s.n} respondent(s)<br>
    LAeq ${(+s.LAeq).toFixed(1)} dB<br>heard: natural ${s.nat}, traffic ${s.tra}, human ${s.hum} (1-5)</p>
    <p class="hint">${unlocked?'':'click the page once to enable sound'}</p>`;
  document.querySelectorAll('tr.hl').forEach(r=>r.classList.remove('hl'));
  document.querySelectorAll(`tr[data-id="${s.id}"]`).forEach(r=>r.classList.add('hl'));
}
function play(s){
  if(!s.src) return;
  if(current!==s.id){ audio.src=s.src; current=s.id; }
  audio.currentTime=0; audio.play().catch(()=>{});
}
function stop(){ audio.pause(); }
for (const s of D.sel){
  const g = el('g',{class:'sel'});
  const mine = String(s.dir).startsWith('chosen');
  el('circle',{cx:X(s.P),cy:Y(s.E),r:mine?8:7,fill:COL[s.cat]||'#888',stroke:mine?'#d00':'#000','stroke-width':mine?3:1},g);
  const t=el('text',{x:X(s.P)+9,y:Y(s.E)+4,'font-size':10},g); t.textContent=s.id;
  g.addEventListener('mouseenter',()=>{show(s); if(!pinned) play(s);});
  g.addEventListener('mouseleave',()=>{ if(!pinned && document.getElementById('stopleave').checked) stop(); });
  g.addEventListener('click',()=>{ if(pinned===s.id){pinned=null; stop();} else {pinned=s.id; show(s); play(s);} });
}
const leg = el('g',{}); let lx=M;
for (const k of ['natural','mixed','urban','']){el("circle",{cx:lx,cy:S+8,r:5,fill:COL[k]},leg);
  const t=el("text",{x:lx+8,y:S+12,"font-size":11},leg); t.textContent=k||'other'; lx+=80;}
// table players: only one plays at a time
document.querySelectorAll('table audio').forEach(a=>a.addEventListener('play',()=>{stop();
  document.querySelectorAll('table audio').forEach(b=>{if(b!==a)b.pause();});}));
</script>
"""


if __name__ == "__main__":
    main()
