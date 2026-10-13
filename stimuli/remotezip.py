"""Download single files out of a zip archive on a web server, without downloading the whole archive.

How it works: the zip index (at the end of the archive) is read with a few small
HTTP range requests. For each wanted file, its compressed bytes are then
downloaded as one byte range into a .zpart file. If the connection drops, the
download continues from the last byte received - also when the script is started
again later. When all bytes are there, the file is decompressed locally and its
CRC checksum is compared with the one stored in the archive; only then does it
get its final name.
"""
from __future__ import annotations

import io
import json
import struct
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from pathlib import Path

CHUNK = 1 << 20          # 1 MB per read while streaming


def _urlopen(url: str, headers: dict, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)


def _wait_time(exc: Exception, attempt: int) -> int:
    """Longer pauses for server-side errors (504/503/429), shorter for dropped connections."""
    if isinstance(exc, urllib.error.HTTPError) and exc.code in (429, 502, 503, 504):
        return min(30 * (attempt + 1), 300)
    return min(5 * (attempt + 1), 60)


def _short(exc: Exception) -> str:
    s = f"{type(exc).__name__}: {exc}"
    return s if len(s) < 70 else s[:67] + "..."


class HTTPRangeFile(io.RawIOBase):
    """Seekable, read-only file object backed by HTTP range requests (used for the zip index)."""

    def __init__(self, url: str, retries: int = 8):
        self.url = url
        self.retries = retries
        self.pos = 0
        rng, status = "", 0
        for attempt in range(retries):
            try:
                with _urlopen(url, {"Range": "bytes=0-0"}) as r:
                    rng, status = r.headers.get("Content-Range", ""), r.status
                break
            except Exception as exc:
                if attempt == retries - 1:
                    raise
                wait = _wait_time(exc, attempt)
                print(f"    reading archive index: {_short(exc)}, retrying in {wait}s")
                time.sleep(wait)
        if status != 206 or "/" not in rng:
            raise OSError(f"server does not support range requests: {url}")
        self.size = int(rng.rsplit("/", 1)[1])

    def get(self, start: int, end: int) -> bytes:
        for attempt in range(self.retries):
            try:
                with _urlopen(self.url, {"Range": f"bytes={start}-{end}"}) as r:
                    data = r.read()
                if len(data) == end - start + 1:
                    return data
                raise OSError(f"short read: {len(data)} of {end - start + 1} bytes")
            except Exception as exc:
                if attempt == self.retries - 1:
                    raise
                wait = _wait_time(exc, attempt)
                print(f"    reading archive index: {_short(exc)}, retrying in {wait}s")
                time.sleep(wait)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: 0, 1: self.pos, 2: self.size}[whence] + offset
        return self.pos

    def readinto(self, b) -> int:
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        data = self.get(self.pos, self.pos + n - 1)
        b[: len(data)] = data
        self.pos += len(data)
        return len(data)


class Progress:
    """One-line status bar: name, bar, percent, MB, speed, remaining time."""

    def __init__(self, label: str, total: int):
        self.label, self.total = label, max(total, 1)
        self.t0 = time.time()
        self.start_bytes = None
        self.last = 0.0
        self.interactive = sys.stdout.isatty()

    def update(self, done: int, note: str = "", force: bool = False) -> None:
        now = time.time()
        if self.start_bytes is None:
            self.start_bytes = done
            self.t0 = now
        if not force and now - self.last < (0.25 if self.interactive else 15):
            return
        self.last = now
        frac = min(done / self.total, 1.0)
        filled = int(frac * 24)
        bar = "#" * filled + "-" * (24 - filled)
        speed = (done - self.start_bytes) / max(now - self.t0, 1e-6)
        eta = (self.total - done) / speed if speed > 0 else 0
        eta_s = f"{int(eta // 60)}:{int(eta % 60):02d}" if speed > 0 else "--:--"
        line = (f"  {self.label:<20} [{bar}] {frac * 100:5.1f}%  {done / 1e6:5.0f}/{self.total / 1e6:.0f} MB"
                f"  {speed / 1e6:5.2f} MB/s  ETA {eta_s}")
        if note:
            line += f"  {note}"
        if self.interactive:
            sys.stdout.write("\r" + line[:150].ljust(150))
        else:
            sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def finish(self, note: str) -> None:
        self.update(self.total, note, force=True)
        if self.interactive:
            sys.stdout.write("\n")
        sys.stdout.flush()

    def newline(self) -> None:
        if self.interactive:
            sys.stdout.write("\n")
            sys.stdout.flush()


class RemoteZip:
    """Index of a remote zip archive. The index is cached in a small JSON file (cache),
    so later runs do not need to read it again from the server."""

    def __init__(self, url: str, retries: int = 8, cache: Path | None = None):
        self.url = url
        self.retries = retries
        self.cache = cache
        self._raw = None
        self.members = None
        if cache and cache.exists():
            try:
                saved = json.loads(cache.read_text())
                if saved.get("url") == url:
                    self.members = saved["members"]
            except Exception:
                self.members = None
        if self.members is None:
            print(f"  reading the archive index from the server (can take a few minutes when the server is slow) ...",
                  flush=True)
            zf = zipfile.ZipFile(io.BufferedReader(self.raw, buffer_size=256 << 10))
            self.members = {i.filename: {"offset": i.header_offset, "csize": i.compress_size, "size": i.file_size,
                                         "crc": i.CRC, "method": i.compress_type} for i in zf.infolist()}
            self._save()

    @property
    def raw(self) -> HTTPRangeFile:
        if self._raw is None:
            self._raw = HTTPRangeFile(self.url, self.retries)
        return self._raw

    def _save(self) -> None:
        if self.cache:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps({"url": self.url, "members": self.members}))

    def names(self) -> list[str]:
        return list(self.members)

    def size(self, name: str) -> int:
        return self.members[name]["size"]

    def _data_range(self, name: str) -> tuple[int, int]:
        """Byte range of the member's compressed data inside the archive (cached after the first time)."""
        m = self.members[name]
        if "start" not in m:
            header = self.raw.get(m["offset"], m["offset"] + 29)
            if header[:4] != b"PK\x03\x04":
                raise OSError(f"unexpected local header for {name}")
            name_len, extra_len = struct.unpack("<HH", header[26:30])
            m["start"] = m["offset"] + 30 + name_len + extra_len
            self._save()
        return m["start"], m["start"] + m["csize"] - 1

    def extract(self, name: str, dest: Path, label: str | None = None) -> Path:
        """Download one member to dest, resuming a previous partial download if present."""
        m = self.members[name]
        if m["method"] not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise OSError(f"unsupported compression in {name}")
        start, end = self._data_range(name)
        total = end - start + 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        old_part = dest.with_name(dest.name + ".part")   # left by an older version of this script
        if old_part.exists():
            old_part.unlink()
        raw_part = dest.with_name(dest.name + ".zpart")  # compressed bytes received so far
        have = raw_part.stat().st_size if raw_part.exists() else 0
        if have > total:
            raw_part.unlink()
            have = 0
        bar = Progress(label or dest.name, total)
        bar.update(have, "resuming" if have else "", force=True)

        attempt = 0
        while have < total:
            try:
                with _urlopen(self.url, {"Range": f"bytes={start + have}-{end}"}) as r, \
                        open(raw_part, "ab") as f:
                    while True:
                        chunk = r.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        have += len(chunk)
                        bar.update(have)
                        attempt = 0          # progress was made: reset the retry counter
                if have < total:
                    raise OSError("connection closed early")
            except KeyboardInterrupt:
                bar.newline()
                print(f"  interrupted - {have / 1e6:.0f} MB kept in {raw_part.name}; run again to continue")
                raise
            except Exception as exc:
                attempt += 1
                if attempt > self.retries:
                    bar.newline()
                    raise
                wait = _wait_time(exc, attempt - 1)
                bar.update(have, f"retry {attempt}/{self.retries} in {wait}s ({_short(exc)})", force=True)
                time.sleep(wait)

        bar.update(total, "checking ...", force=True)
        tmp = dest.with_name(dest.name + ".unpack")
        crc = 0
        with open(raw_part, "rb") as src, open(tmp, "wb") as out:
            dec = zlib.decompressobj(-15) if m["method"] == zipfile.ZIP_DEFLATED else None
            while block := src.read(CHUNK * 4):
                data = dec.decompress(block) if dec else block
                crc = zlib.crc32(data, crc)
                out.write(data)
            if dec:
                tail = dec.flush()
                crc = zlib.crc32(tail, crc)
                out.write(tail)
        if crc != m["crc"] or tmp.stat().st_size != m["size"]:
            tmp.unlink()
            raw_part.unlink()
            bar.finish("checksum wrong - partial file removed")
            raise OSError(f"checksum mismatch for {name}")
        tmp.rename(dest)
        raw_part.unlink()
        bar.finish("ok")
        return dest
