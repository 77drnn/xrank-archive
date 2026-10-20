#!/usr/bin/env python3
"""Prepare only public website assets for GitHub Pages deployment."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "public"
FILES = [".nojekyll", "index.html", "style.css", "app.js", "sw.js", "manifest.webmanifest", "icon.svg", "icon-180.png", "icon-192.png", "icon-512.png"]

def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for name in FILES:
        src = ROOT / name
        if not src.exists():
            raise FileNotFoundError(f"required site file missing: {src}")
        shutil.copy2(src, OUT / name)
    data = ROOT / "data"
    if data.exists():
        shutil.copytree(data, OUT / "data", ignore=shutil.ignore_patterns("archive-state.json", "archive-manifest-catalog.json", "all-time-archive-max.json"))
    print(f"GitHub Pages site prepared: {OUT}")

if __name__ == "__main__":
    main()
