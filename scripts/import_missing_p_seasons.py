#!/usr/bin/env python3
"""Download previously unrecorded P-season X-ranking JSON files from splatoon3.ink."""
from __future__ import annotations
import re, time
from urllib.parse import urljoin, urlparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

ROOT = Path(__file__).resolve().parents[1]
INDEX_URL = "https://assets.splatoon3.ink/data/xrank/"
OUT = ROOT / "data" / "source-xrank"
RULES = {"splatzones", "towercontrol", "rainmaker", "clamblitz"}
PATTERN = re.compile(r"^xrank\.detail\.(p-\d+)\.(splatzones|towercontrol|rainmaker|clamblitz)(\.weapons)?\.json$", re.I)

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href: self.hrefs.append(href.rsplit("/",1)[-1])

def get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent":"xrank-archive-p-season-collector/1.0"})
    with urlopen(req, timeout=45) as response:
        return response.read()

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    page = get(INDEX_URL).decode("utf-8", "replace")
    parser = Links(); parser.feed(page)
    # The listing normally exposes links, but also scan its text as a fallback
    # in case the asset browser changes how filenames are rendered.
    discovered = set()
    for href in parser.hrefs:
        name = urlparse(href).path.rstrip("/").rsplit("/", 1)[-1]
        if PATTERN.match(name):
            discovered.add(name)
    discovered.update(re.findall(
        r"xrank\.detail\.p-\d+\.(?:splatzones|towercontrol|rainmaker|clamblitz)(?:\.weapons)?\.json",
        page,
        flags=re.I,
    ))
    names = sorted(discovered, key=lambda n: (int(PATTERN.match(n).group(1)[2:]), n))
    if not names:
        raise SystemExit(f"No P-season X ranking files discovered at {INDEX_URL}")
    downloaded=skipped=failed=0
    for name in names:
        m=PATTERN.match(name); season=m.group(1).lower()
        dest=OUT / season / name
        if dest.exists() and dest.stat().st_size > 0:
            skipped += 1; continue
        url=urljoin(INDEX_URL, name)
        try:
            data=get(url)
            # Guard against HTML error pages being saved as JSON.
            if not data.lstrip().startswith(b"{"):
                raise ValueError("response is not JSON")
            import json
            parsed=json.loads(data)
            if not isinstance(parsed,dict) or "data" not in parsed:
                raise ValueError("unexpected JSON structure")
            dest.parent.mkdir(parents=True,exist_ok=True)
            tmp=dest.with_suffix(dest.suffix+".tmp"); tmp.write_bytes(data); tmp.replace(dest)
            downloaded += 1
            print(f"Downloaded {season}/{name} ({len(data):,} bytes)")
            time.sleep(0.15)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            failed += 1; print(f"WARN: {name}: {exc}")
    print(f"P-season source import complete: downloaded={downloaded}, already_present={skipped}, failed={failed}")
    if failed:
        raise SystemExit(1)

if __name__ == "__main__": main()
