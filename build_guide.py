#!/usr/bin/env python3
"""Build one combined TV guide (XMLTV) for the playlist.

Reads channels.json (which guide source each channel's listings come from),
downloads each source, keeps only our channels and the next ~8 days,
and writes site/guide.xml.gz for GitHub Pages.

Uses the Python standard library only (no third-party packages).
"""
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

MIN_PYTHON = (3, 9)
HERE = os.path.dirname(os.path.abspath(__file__))
CHANNELS_FILE = os.path.join(HERE, "channels.json")
OUT_DIR = os.path.join(HERE, "site")
OUT_FILE = os.path.join(OUT_DIR, "guide.xml.gz")
PREVIOUS_URL = "https://lanternbox-star.github.io/tv-guide/guide.xml.gz"

USER_AGENT = "tv-guide-builder/1.0 (+https://github.com/lanternbox-star/tv-guide)"
TIMEOUT = 120               # seconds per request
RETRIES = 3
MAX_DOWNLOAD = 600 * 1024 * 1024   # refuse anything bigger than 600 MB
KEEP_PAST = timedelta(hours=12)
KEEP_FUTURE = timedelta(days=8)
MIN_COVERAGE = 0.5          # refuse to publish if under half the channels have listings


def fail(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def prerequisite_check():
    """Refuse to run, with a clear message, if requirements aren't met."""
    if sys.version_info < MIN_PYTHON:
        fail("Python %d.%d or newer is required (this is %d.%d). "
             "Install a newer Python from https://www.python.org/downloads/"
             % (MIN_PYTHON + sys.version_info[:2]))
    if not os.path.isfile(CHANNELS_FILE):
        fail(f"channels.json not found next to this script ({CHANNELS_FILE}).")
    try:
        with open(CHANNELS_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        sources = cfg["sources"]
        assert isinstance(sources, list) and sources
        for s in sources:
            assert s["url"].startswith("https://"), f"{s['key']}: only https sources are allowed"
            assert isinstance(s["channels"], list)
    except Exception as e:  # noqa: BLE001
        fail(f"channels.json is not valid: {e}")
    try:
        os.makedirs(OUT_DIR, exist_ok=True)
        probe = os.path.join(OUT_DIR, ".write-test")
        open(probe, "w").close()
        os.remove(probe)
    except OSError as e:
        fail(f"Cannot write to the output folder {OUT_DIR}: {e}")
    return sources


def download(url, dest):
    """Download url to dest (decompressing gzip). Returns True on success."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept-Encoding": "identity"})
            raw = dest + ".raw"
            size = 0
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r, open(raw, "wb") as f:
                while True:
                    chunk = r.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise IOError("download is larger than the safety limit")
                    f.write(chunk)
            with open(raw, "rb") as f:
                magic = f.read(2)
            if magic == b"\x1f\x8b":
                with gzip.open(raw, "rb") as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                os.remove(raw)
            else:
                os.replace(raw, dest)
            return True
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"   attempt {attempt} failed: {e}")
            time.sleep(10 * attempt)
    print(f"   giving up on {url}: {last}")
    return False


def parse_time(value):
    """XMLTV time like '20261120180000 +1100' -> aware datetime (None if unreadable)."""
    try:
        value = value.strip()
        base = datetime.strptime(value[:14], "%Y%m%d%H%M%S")
        off = value[14:].strip()
        if off and off[0] in "+-" and len(off) >= 5:
            sign = 1 if off[0] == "+" else -1
            delta = timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
            return base.replace(tzinfo=timezone(sign * delta))
        return base.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def extract(xml_path, wanted, channels_out, prog_out, now):
    """Copy <channel> and in-window <programme> elements for wanted ids.
    Returns (channels found, programmes kept, set of ids with programmes)."""
    lo, hi = now - KEEP_PAST, now + KEEP_FUTURE
    n_prog, with_prog = 0, set()
    found = set()
    context = ET.iterparse(xml_path, events=("start", "end"))
    _, root = next(context)
    for event, el in context:
        if event != "end":
            continue
        if el.tag == "channel":
            cid = el.get("id")
            if cid in wanted and cid not in found:
                found.add(cid)
                channels_out[cid] = ET.tostring(el, encoding="unicode")
            root.clear()
        elif el.tag == "programme":
            cid = el.get("channel")
            if cid in wanted:
                start, stop = parse_time(el.get("start", "")), parse_time(el.get("stop", "") or el.get("start", ""))
                if start and stop and stop >= lo and start <= hi:
                    el.tail = "\n"
                    prog_out.write(ET.tostring(el, encoding="unicode").encode("utf-8"))
                    n_prog += 1
                    with_prog.add(cid)
            root.clear()
    return found, n_prog, with_prog


def main():
    sources = prerequisite_check()
    now = datetime.now(timezone.utc)
    work = tempfile.mkdtemp(prefix="guide-")
    channels_out = {}
    prog_path = os.path.join(work, "programmes.xml")
    expected = set()
    covered = set()
    failed = []
    report = []

    with open(prog_path, "wb") as prog_out:
        for s in sources:
            wanted = set(s["channels"])
            expected |= wanted
            print(f"== {s['name']} ({len(wanted)} channels)")
            path = os.path.join(work, s["key"] + ".xml")
            if not download(s["url"], path):
                failed.append(s)
                report.append({"source": s["name"], "status": "download failed"})
                continue
            try:
                found, n, with_prog = extract(path, wanted, channels_out, prog_out, now)
            except ET.ParseError as e:
                print(f"   could not read guide: {e}")
                failed.append(s)
                report.append({"source": s["name"], "status": "unreadable"})
                continue
            finally:
                os.remove(path)
            covered |= with_prog
            print(f"   {len(with_prog)} channels with listings, {n} programmes")
            report.append({"source": s["name"], "status": "ok",
                           "channels_with_listings": len(with_prog), "programmes": n})

        # A source that failed today keeps yesterday's listings from the live guide.
        if failed:
            wanted = set().union(*(set(s["channels"]) for s in failed))
            print(f"== Re-using previous guide for {len(failed)} failed source(s)")
            path = os.path.join(work, "previous.xml")
            if download(PREVIOUS_URL, path):
                try:
                    _, n, with_prog = extract(path, wanted, channels_out, prog_out, now)
                    covered |= with_prog
                    print(f"   kept {n} programmes for {len(with_prog)} channels")
                except ET.ParseError as e:
                    print(f"   previous guide unreadable: {e}")

    coverage = len(covered) / max(1, len(expected))
    print(f"Coverage: {len(covered)}/{len(expected)} channels ({coverage:.0%})")
    if coverage < MIN_COVERAGE:
        fail("Too few channels have listings; not publishing so the current guide stays live.", 1)

    tmp_out = OUT_FILE + ".tmp"
    with gzip.open(tmp_out, "wb", compresslevel=9) as out:
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(b'<tv generator-info-name="lanternbox-star/tv-guide">\n')
        for cid in sorted(channels_out):
            out.write(channels_out[cid].strip().encode("utf-8") + b"\n")
        with open(prog_path, "rb") as p:
            shutil.copyfileobj(p, out)
        out.write(b"</tv>\n")
    os.replace(tmp_out, OUT_FILE)
    shutil.rmtree(work, ignore_errors=True)

    status = {"built_utc": now.strftime("%Y-%m-%d %H:%M"),
              "channels_expected": len(expected),
              "channels_with_listings": len(covered),
              "size_bytes": os.path.getsize(OUT_FILE),
              "sources": report}
    with open(os.path.join(OUT_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, indent=1)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write("<!doctype html><meta charset='utf-8'><title>TV guide</title>"
                "<p><a href='guide.xml.gz'>guide.xml.gz</a> &middot; "
                "<a href='status.json'>status.json</a></p>\n")
    print(f"Wrote {OUT_FILE} ({status['size_bytes'] / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
