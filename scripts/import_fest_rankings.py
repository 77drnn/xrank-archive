#!/usr/bin/env python3
"""Import Japanese festival ranking JSON files from splatoon3.ink and build a festival archive.

The upstream directory listing is inspected dynamically so newly added festival files are
picked up without maintaining a hard-coded festival list. The source has changed naming
conventions over time; parsing is deliberately tolerant and logs unknown files/records.
"""
from __future__ import annotations
import json, re, time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
INDEX_URL = "https://assets.splatoon3.ink/data/xrank/"
SOURCE_DIR = ROOT / "data" / "source-fest-jp"
OUTPUT = ROOT / "data" / "fest-rankings.json"

class Links(HTMLParser):
    def __init__(self): super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href: self.hrefs.append(href.rsplit("/",1)[-1])

def get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent":"xrank-archive-festival-collector/1.0"})
    with urlopen(req, timeout=45) as response: return response.read()

def is_jp_fest_file(name: str) -> bool:
    low = name.lower()
    if not low.endswith(".json") or not (any(x in low for x in ("fest", "festival", "splatfest")) or re.search(r"xrank\.detail\.f(?:-|\.)", low)):
        return False
    # Include JP-labelled files and locale-neutral files; exclude explicitly non-JP regions.
    if re.search(r"(?:^|[._-])(en|us|eu|ap|na|kr|tw|hk)(?:[._-]|$)", low): return False
    return True

def fest_key(name: str) -> str:
    key = re.sub(r"\.json$", "", name, flags=re.I)
    key = re.sub(r"^xrank\.detail\.", "", key, flags=re.I)
    key = re.sub(r"^(?:splat)?fest(?:ival)?[._-]*", "fest-", key, flags=re.I)
    key = re.sub(r"[._-](?:jp|ja|jpn)(?=[._-]|$)", "", key, flags=re.I)
    key = re.sub(r"[._-](?:en|us|eu|ap|na|kr|tw|hk)(?=[._-]|$)", "", key, flags=re.I)
    key = re.sub(r"[._-](?:team|faction|side)[._-]?[a-z0-9-]+$", "", key, flags=re.I)
    key = re.sub(r"(?:[._-](?:team|faction|side)[._-]?[a-z0-9-]+)$", "", key, flags=re.I)
    # If the upstream file encodes each festival by numeric ID, use that ID so team files merge.
    match = re.search(r"((?:splat)?fest(?:ival)?[._-](?:p[._-]?)?\d+)", key, flags=re.I)
    if match: return match.group(1).replace("_", "-")
    return key.strip("._-") or Path(name).stem

def first_value(obj: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None

def display_text(value):
    if isinstance(value, str): return value
    if isinstance(value, dict):
        for k in ("name", "text", "label", "value", "teamName"):
            if value.get(k) not in (None, ""): return str(value[k])
    return ""

def extract_players(data, filename: str):
    """Walk nested JSON and collect objects that look like ranked festival players."""
    found=[]
    def walk(node, path=(), inherited_team=""):
        if isinstance(node, dict):
            team = inherited_team
            for key in ("team", "teamName", "team_name", "faction", "factionName", "side"):
                if key in node:
                    candidate=display_text(node[key])
                    if candidate and len(candidate) < 80: team=candidate
            name = first_value(node, ("name", "playerName", "player_name", "nickname"))
            rank = first_value(node, ("rank", "ranking", "position", "place"))
            power = first_value(node, ("festPower", "fest_power", "festivalPower", "festival_power", "power", "x_power"))
            # Avoid collecting aggregate/team objects as players.
            if name is not None and rank is not None and power is not None:
                try:
                    rank_num=int(rank)
                    power_num=float(power)
                    if rank_num > 0 and power_num > 0:
                        name=str(name).strip()
                        name_id=first_value(node, ("nameId", "name_id", "discriminator", "nameID"))
                        stable_id=first_value(node, ("player_key", "playerKey", "playerId", "player_id", "uniqueId", "unique_id"))
                        team_value=team or display_text(first_value(node, ("team", "faction", "side")))
                        # Public ranking feeds do not always expose a persistent account ID.
                        identity = str(stable_id) if stable_id is not None else (f"nameId:{name_id}|name:{name}" if name_id is not None else f"name:{name}")
                        found.append({
                            "player_key": f"fest-player:{identity}", "name": name,
                            "name_id": str(name_id or ""), "rank": rank_num,
                            "fest_power": round(power_num, 1), "team": team_value or "陣営不明",
                            "source_file": filename,
                        })
                except (TypeError, ValueError): pass
            for k,v in node.items(): walk(v, path+(str(k),), team)
        elif isinstance(node, list):
            for i,v in enumerate(node): walk(v, path+(str(i),), inherited_team)
    walk(data)
    # De-duplicate repeated nested references, keeping the best rank/power row.
    unique={}
    for p in found:
        key=(p["player_key"],p["team"],p["rank"],p["fest_power"])
        unique[key]=p
    return list(unique.values())

def main():
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    html=get(INDEX_URL).decode("utf-8", "replace")
    parser=Links(); parser.feed(html)
    names=sorted({n for n in parser.hrefs if is_jp_fest_file(n)})
    if not names:
        raise SystemExit(f"No festival JSON files discovered at {INDEX_URL}. Check the current upstream filename pattern.")
    downloaded=skipped=0; failures=[]; festivals=[]
    for name in names:
        dest=SOURCE_DIR/name
        try:
            # Re-fetch every run so active/current festival rankings update; keep prior files when fetch fails.
            raw=get(INDEX_URL+name)
            if not raw.lstrip().startswith(b"{"): raise ValueError("response is not a JSON object")
            parsed=json.loads(raw)
            if not isinstance(parsed,dict): raise ValueError("JSON root is not an object")
            dest.write_bytes(raw); downloaded += 1
            rows=extract_players(parsed,name)
            if not rows:
                print(f"WARN: {name}: no ranked festival-player records recognized")
                continue
            key=fest_key(name)
            # If the same festival/team is represented by multiple files, merge below.
            title = first_value(parsed, ("festivalName", "festName", "festival_name", "eventName", "event_name", "title"))
            festivals.append({"key":key,"title":str(title or key),"region":"JP","source_file":name,"players":rows})
            print(f"Imported {name}: {len(rows)} rows")
            time.sleep(0.1)
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
            failures.append(f"{name}: {exc}"); print(f"WARN: {failures[-1]}")
    if not festivals:
        raise SystemExit("Festival files were found but none contained recognizable ranked player records; no output was replaced.")
    # Merge files that refer to the same festival, but retain team labels and all rows.
    merged={}
    for fest in festivals:
        key=fest["key"]
        bucket=merged.setdefault(key,{"key":key,"title":fest.get("title") or key,"region":"JP","source_files":[],"players":[]})
        bucket["source_files"].append(fest["source_file"])
        bucket["players"].extend(fest["players"])
    final_fests=[]; all_players=[]
    for key,fest in merged.items():
        dedup={}
        for p in fest["players"]:
            # Keep same player across teams/ranks as distinct event placements only by team.
            dedup[(p["player_key"],p["team"])]=p
        players=sorted(dedup.values(),key=lambda p:(p["rank"],-p["fest_power"],p["name"]))
        fest["players"]=players
        final_fests.append(fest); all_players.extend([{**p,"festival_key":key,"festival_title":fest["title"]} for p in players])
    final_fests.sort(key=lambda f:(max([int(x) for x in re.findall(r"\d+", f["key"])] or [0]), f["key"]),reverse=True)
    best={}
    for p in all_players:
        key=p["player_key"]
        if key not in best or p["fest_power"]>best[key]["fest_power"]:
            best[key]=p
    top=sorted(best.values(),key=lambda p:(-p["fest_power"],p["rank"],p["name"]))[:1000]
    for i,p in enumerate(top,1): p["all_time_rank"]=i
    output={"schema_version":1,"generated_at":datetime.now(timezone.utc).isoformat(),"region":"JP","festivals":final_fests,"all_time_top1000":top,"source_url":INDEX_URL,"downloaded_files":downloaded,"failed_files":failures}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUTPUT.with_suffix(".json.tmp"); tmp.write_text(json.dumps(output,ensure_ascii=False,separators=(",",":")),encoding="utf-8"); tmp.replace(OUTPUT)
    print(f"Wrote {OUTPUT}: festivals={len(final_fests)}, placements={len(all_players)}, all-time top={len(top)}, downloaded={downloaded}, failed={len(failures)}")
    if failures: raise SystemExit("Some festival files failed: " + "; ".join(failures[:5]))

if __name__ == "__main__": main()
