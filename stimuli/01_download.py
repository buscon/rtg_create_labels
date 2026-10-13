"""Step 1: download candidate recordings from USotW and EigenScape.

Only the recordings you ask for are downloaded: the scripts read single files out
of the Zenodo archives (HTTP range requests), so there is no need to download the
full archives (several GB for USotW, 12.6 GB for EigenScape Lite).

Examples
    # metadata of both datasets only (a few kB), to look at the lists first
    python stimuli/01_download.py --metadata-only

    # the 20 quietest USotW recordings + EigenScape Woodland, Beach and Park
    python stimuli/01_download.py --usotw-quietest 20 --eigenscape-classes Woodland Beach Park

    # specific recordings
    python stimuli/01_download.py --usotw R0124 R0051 R0052 --eigenscape Woodland-05 Woodland-06

Sizes: USotW recordings are 60 s of first-order ambisonics (~35 MB each);
EigenScape Lite recordings are 10 min of first-order ambisonics as FLAC (~200 MB each).
Files go to stimuli/raw/ (not in git). Each file shows a status bar. Complete
files are skipped; an interrupted file continues from where it stopped (the
partial data is kept as <name>.zpart). Dropped connections and server time-outs
are retried automatically; files that still fail are listed at the end with the
archive link and an unzip command for a manual download.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CLASSES, EIGENSCAPE, RAW, USOTW, eigenscape_member, read_csv  # noqa: E402
from remotezip import RemoteZip  # noqa: E402


def fetch(url: str, dest: Path, retries: int = 5) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            dest.write_bytes(data)
            return dest
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3 * (attempt + 1))
    return dest


def download_usotw(ids: list[str], retries: int) -> list[dict]:
    out = RAW / "usotw"
    wanted = {f"{i}_segment_ambisonics.wav" for i in ids}
    todo = {n for n in wanted if not (out / n).exists()}
    print(f"USotW: {len(wanted)} recordings requested, {len(wanted) - len(todo)} already present")
    failed = []
    for zname in USOTW["zips"]:
        if not todo:
            break
        url = USOTW["base"] + zname + "?download=1"
        try:
            rz = RemoteZip(url, retries, cache=out / f".index_{zname}.json")
        except Exception as exc:
            print(f"  cannot read the index of {zname}: {exc}")
            failed += [{"name": n, "archive": zname, "url": url, "member": n, "dest": out} for n in sorted(todo)]
            continue
        by_base = {n.rsplit("/", 1)[-1]: n for n in rz.names()}   # members may sit in a sub-folder
        for name in sorted(todo & by_base.keys()):
            try:
                rz.extract(by_base[name], out / name)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  failed: {name} ({exc})")
                failed.append({"name": name, "archive": zname, "url": url, "member": by_base[name], "dest": out})
            todo.discard(name)
    for name in sorted(todo):
        if not any(f["name"] == name for f in failed):
            print(f"  not found in the archives: {name}")
    return failed


def download_eigenscape(files: list[str], retries: int) -> list[dict]:
    out = RAW / "eigenscape"
    members = {eigenscape_member(f): f for f in files}
    todo = sorted(m for m in members if not (out / m).exists())
    print(f"EigenScape: {len(members)} recordings requested, {len(members) - len(todo)} already present")
    if not todo:
        return []
    url = EIGENSCAPE["base"] + EIGENSCAPE["zip"] + "?download=1"
    try:
        rz = RemoteZip(url, retries, cache=out / f".index_{EIGENSCAPE['zip']}.json")
    except Exception as exc:
        print(f"  cannot read the index of {EIGENSCAPE['zip']}: {exc}")
        return [{"name": m, "archive": EIGENSCAPE["zip"], "url": url, "member": m, "dest": out} for m in todo]
    by_base = {n.rsplit("/", 1)[-1]: n for n in rz.names()}
    failed = []
    for i, m in enumerate(todo, start=1):
        if m not in by_base:
            print(f"  not found in the archive: {m}")
            continue
        try:
            rz.extract(by_base[m], out / m, label=f"{m} ({i}/{len(todo)})")
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"  failed: {m} ({exc})")
            failed.append({"name": m, "archive": EIGENSCAPE["zip"], "url": url, "member": by_base[m], "dest": out})
    return failed


def print_manual_instructions(failed: list[dict]) -> None:
    """Zenodo has no direct link per file inside an archive: download the archive, extract the files."""
    print(f"\n{len(failed)} file(s) could not be downloaded. Run the script again later (it continues where it stopped),")
    print("or download the archive yourself (browser or download manager) and extract only these files:\n")
    by_archive: dict[str, list[dict]] = {}
    for f in failed:
        by_archive.setdefault(f["archive"], []).append(f)
    for archive, items in by_archive.items():
        dest = items[0]["dest"]
        print(f"  archive:  {archive}")
        print(f"  link:     {items[0]['url']}")
        print(f"  files:    {', '.join(i['member'] for i in items)}")
        members = " ".join(f'"{i["member"]}"' for i in items)
        print(f"  extract:  unzip -j {archive} {members} -d \"{dest}\"\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--metadata-only", action="store_true", help="download only the two metadata files")
    ap.add_argument("--usotw", nargs="*", default=[], metavar="ID", help="USotW recording IDs, e.g. R0124")
    ap.add_argument("--usotw-quietest", type=int, default=0, metavar="N", help="the N quietest USotW recordings (LAeq)")
    ap.add_argument("--usotw-all", action="store_true", help="all USotW recordings with ambisonics (~4 GB)")
    ap.add_argument("--eigenscape", nargs="*", default=[], metavar="NAME", help="EigenScape recordings, e.g. Woodland-05")
    ap.add_argument("--eigenscape-classes", nargs="*", default=[], choices=CLASSES, metavar="CLASS",
                    help=f"all 8 recordings of these classes ({', '.join(CLASSES)})")
    ap.add_argument("--retries", type=int, default=8,
                    help="retries per file without progress before giving up (default 8)")
    args = ap.parse_args()

    u_meta = fetch(USOTW["base"] + USOTW["metadata"] + "?download=1", RAW / "usotw" / USOTW["metadata"])
    e_meta = fetch(EIGENSCAPE["base"] + EIGENSCAPE["metadata"] + "?download=1", RAW / "eigenscape" / EIGENSCAPE["metadata"])
    print(f"metadata: {u_meta}\n          {e_meta}")
    if args.metadata_only:
        return

    usotw = [r for r in read_csv(u_meta) if r.get("Ambisonic filename")]
    ids = set(args.usotw)
    if args.usotw_all:
        ids |= {r["Recording"] for r in usotw}
    if args.usotw_quietest:
        usotw.sort(key=lambda r: float(r["LAeq,1min"]))
        ids |= {r["Recording"] for r in usotw[: args.usotw_quietest]}
    failed = []
    if ids:
        failed += download_usotw(sorted(ids), args.retries)

    eig = read_csv(e_meta)
    names = {f if f.endswith(".wav") else f + ".wav" for f in args.eigenscape}
    names |= {r["Filename"] for r in eig if r["Class"] in args.eigenscape_classes}
    if names:
        failed += download_eigenscape(sorted(names), args.retries)
    if failed:
        print_manual_instructions(failed)
        sys.exit(1)
    print("done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped. Run the same command again to continue.")
        sys.exit(130)
