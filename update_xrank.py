#!/usr/bin/env python3
"""Download X Match rankings for every season exposed by splatoon3.ink.

Only X-ranking JSON files are retrieved. Festivals, schedules, gear, and other
reference datasets are deliberately excluded.
"""
from __future__ import annotations
import argparse
import base64
import io
import json
import re
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

try:
    import zstandard as zstd
except ImportError:  # Installed by requirements.txt in GitHub Actions
    zstd = None

ROOT = 'https://assets.splatoon3.ink/data/'
XRANK_DIR = urljoin(ROOT, 'xrank/')
ARCHIVE_ROOT = urljoin(ROOT, 'archive/')
ARCHIVE_CDN = 'https://data-archive.splatoon3.ink/'
RULES = ('splatzones', 'towercontrol', 'rainmaker', 'clamblitz')
RULE_JA = {'splatzones':'ガチエリア','towercontrol':'ガチヤグラ','rainmaker':'ガチホコ','clamblitz':'ガチアサリ'}
USER_AGENT = 'XRankWebArchive/2.0 (public X-ranking archive)'
HOURLY_RETENTION_DAYS = 90
ARCHIVE_RECENT_MANIFESTS = 12
# Backfill as much of the historical archive as practical in one cloud job.
# The GitHub Actions job limit is 45 minutes; leave time for index rebuild, commit and deploy.
ARCHIVE_BACKFILL_MAX_SECONDS = 30 * 60
ARCHIVE_HISTORY_SHARDS = 32
ARCHIVE_HISTORY_FORMAT_VERSION = 2
ALL_TIME_RANKING_VERSION = 1
MIN_ARCHIVE_RANKING_BYTES = 500
ARCHIVE_FILE_RE = re.compile(r'(?P<date>\d{4}-\d{2}-\d{2})\.(?P<time>\d{2}-\d{2}-\d{2})\.xrank\.detail\.(?P<season>p-\d+)\.(?P<rule>splatzones|towercontrol|rainmaker|clamblitz)\.json$', re.I)
SEASON_FILE_RE = re.compile(r'p-\d+$', re.I)
_JSONL_DATE_CACHE: dict[str,set[str]] = {}
_ACTIVE_ARCHIVE_MAX: dict[str, Any] | None = None

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.hrefs=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'a':
            for k,v in attrs:
                if k.lower() == 'href' and v: self.hrefs.append(v)

def fetch(url: str, timeout: int = 45) -> bytes:
    req = Request(url, headers={'User-Agent':USER_AGENT,'Accept':'application/json,text/html,*/*'})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def get_listing() -> dict[str, dict[str,str]]:
    raw = fetch(XRANK_DIR).decode('utf-8','replace')
    parser = LinkParser(); parser.feed(raw)
    links = parser.hrefs + re.findall(r'''(?:href\s*=\s*["']?)([^\s"'>]+)''', raw, flags=re.I)
    # Some object-storage pages expose filenames as plain text instead of links.
    links += re.findall(r'xrank\.detail\.[a-z]+-\d+\.[a-z]+\.json', raw, flags=re.I)
    pat = re.compile(r'xrank\.detail\.(p-\d+)\.(splatzones|towercontrol|rainmaker|clamblitz)\.json$', re.I)
    found: dict[str,dict[str,str]] = {}
    for link in links:
        clean = link.split('?',1)[0].rstrip('/')
        filename = clean.rsplit('/',1)[-1]
        match = pat.fullmatch(filename)
        if not match: continue
        season, rule = match.group(1).lower(), match.group(2).lower()
        if rule in RULES and SEASON_FILE_RE.fullmatch(season):
            found.setdefault(season,{})[rule]=urljoin(XRANK_DIR, filename)
    complete = {s:r for s,r in found.items() if set(RULES).issubset(r)}
    if not complete:
        raise RuntimeError('Xランキングのシーズン別JSONが一覧から見つかりませんでした。公開ディレクトリの構成が変わった可能性があります。')
    return complete

def sort_key(season: str):
    m=re.fullmatch(r'([a-z]+)-(\d+)',season)
    return (int(m.group(2)) if m else -1, 1 if m and m.group(1)=='p' else 0, m.group(1) if m else season)

def walk(value: Any):
    if isinstance(value,dict):
        yield value
        for v in value.values(): yield from walk(v)
    elif isinstance(value,list):
        for v in value: yield from walk(v)

def decode_uid(raw_id: str):
    try:
        pad=raw_id+'='*(-len(raw_id)%4)
        decoded=base64.urlsafe_b64decode(pad.encode()).decode('utf-8','ignore')
        m=re.search(r':u-([^:]+)$',decoded)
        if m: return 'uid:'+m.group(1)
    except Exception: pass
    return None

def nodes(payload: Any):
    result=[]; seen=set()
    for n in walk(payload):
        if not all(k in n for k in ('id','name','rank','xPower')): continue
        typename=n.get('__typename') or n.get('__isPlayer')
        if typename not in (None,'XRankingHolder') and 'XRankingHolder' not in str(typename): continue
        if not isinstance(n.get('rank'),(int,float)) or not isinstance(n.get('xPower'),(int,float)): continue
        rid=str(n['id'])
        if rid in seen: continue
        seen.add(rid)
        weapon=n.get('weapon') if isinstance(n.get('weapon'),dict) else {}
        sub=weapon.get('subWeapon') if isinstance(weapon.get('subWeapon'),dict) else {}
        special=weapon.get('specialWeapon') if isinstance(weapon.get('specialWeapon'),dict) else {}
        name_id=n.get('nameId')
        pkey=decode_uid(rid) or (f'nameId:{name_id}' if name_id not in (None,'') else f'id:{rid}')
        result.append({
            'player_key':pkey,'id':rid,'name':n.get('name',''),
            'name_id':str(name_id) if name_id is not None else '',
            'discriminator':str(name_id) if name_id is not None else '',
            'rank':n.get('rank'),'rank_diff':n.get('rankDiff'),'x_power':n.get('xPower'),
            'weapon':weapon.get('name'),'weapon_id':weapon.get('id'),
            'sub_weapon':sub.get('name'),'special_weapon':special.get('name'),
            'weapon_top':n.get('weaponTop'),'byname':n.get('byname'),'nameplate':n.get('nameplate')
        })
    result.sort(key=lambda p:(int(p.get('rank',10**9)),str(p.get('name',''))))
    return result

def write_json(path: Path, obj: Any, compact: bool = False):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    if compact:
        body=json.dumps(obj,ensure_ascii=False,separators=(',',':'))
    else:
        body=json.dumps(obj,ensure_ascii=False,indent=2)
    tmp.write_text(body+'\n',encoding='utf-8')
    tmp.replace(path)

def stable_players(players: list[dict[str, Any]]) -> str:
    """Stable canonical representation used to avoid duplicate archive writes."""
    return json.dumps(players,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None

def hourly_player(p: dict[str, Any]) -> list[Any]:
    # Small positional rows keep a long hourly archive compact. Columns:
    # [player_key, name, rank, x_power, weapon, name_id].
    return [p.get('player_key'), p.get('name'), p.get('rank'), p.get('x_power'), p.get('weapon'), p.get('name_id')]

def player_history_shard(player_key: str) -> int:
    """Stable FNV-1a hash shared with app.js; distribute player history across 32 small files."""
    value = 2166136261
    for byte in str(player_key).encode('utf-8'):
        value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value % ARCHIVE_HISTORY_SHARDS


def latest_hourly_snapshot(out: Path, season: str, rule: str) -> dict[str, Any] | None:
    folder=out/'hourly'/season/rule
    if not folder.exists(): return None
    for path in sorted(folder.glob('*.jsonl'),reverse=True):
        try:
            for line in reversed(path.read_text(encoding='utf-8').splitlines()):
                if line.strip():
                    obj=json.loads(line)
                    if isinstance(obj,dict) and isinstance(obj.get('players'),list): return obj
        except (OSError,json.JSONDecodeError):
            continue
    return None

def append_hourly_snapshot(out: Path, season: str, rule: str, players: list[dict[str, Any]]) -> tuple[bool,str|None]:
    """Append a compact snapshot only when observable ranking data changed."""
    compact_players=[hourly_player(p) for p in players]
    # Key order stays stable between snapshots, helping Git store efficient diffs.
    compact_players.sort(key=lambda p:str(p[0] or ''))
    previous=latest_hourly_snapshot(out,season,rule)
    if previous and stable_players(previous.get('players',[])) == stable_players(compact_players):
        return False, str(previous.get('at') or '') or None
    now=datetime.now(timezone.utc).replace(microsecond=0)
    month=now.strftime('%Y-%m')
    stamp=now.isoformat().replace('+00:00','Z')
    target=out/'hourly'/season/rule/f'{month}.jsonl'
    target.parent.mkdir(parents=True,exist_ok=True)
    record={'at':stamp,'season':season,'rule':rule,'players':compact_players}
    with target.open('a',encoding='utf-8',newline='\n') as fh:
        fh.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
    return True,stamp

def build_hourly_index(out: Path, latest_season: str, newest_snapshot_at: str|None):
    old=read_json(out/'hourly-index.json') or {}
    season_map={}
    root=out/'hourly'
    if root.exists():
        for season_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            season_map[season_dir.name]={}
            for rule in RULES:
                folder=season_dir/rule
                months=sorted(p.stem for p in folder.glob('*.jsonl')) if folder.exists() else []
                if months: season_map[season_dir.name][rule]=months
            if not season_map[season_dir.name]: season_map.pop(season_dir.name,None)
    obj={'latest_season':latest_season,'latest_snapshot_at':newest_snapshot_at or old.get('latest_snapshot_at'),'seasons':season_map}
    write_json(out/'hourly-index.json',obj)

def valid_cached(path: Path):
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        return isinstance(data,dict) and isinstance(data.get('players'),list) and bool(data['players'])
    except Exception: return False

def update_heartbeat(out: Path) -> None:
    """Commit a tiny monthly heartbeat so long-idle public repos keep schedules enabled."""
    path=out/'collector-heartbeat.json'
    old=read_json(path) or {}
    try:
        last=datetime.fromisoformat(str(old.get('last_heartbeat_at','')).replace('Z','+00:00'))
        if last.tzinfo is None: last=last.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError):
        last=None
    now=datetime.now(timezone.utc).replace(microsecond=0)
    if last is None or now-last >= timedelta(days=30):
        write_json(path,{'last_heartbeat_at':now.isoformat().replace('+00:00','Z')})


def prune_hourly_history(out: Path) -> bool:
    """Keep the latest 90 days of hourly history to bound static-site size."""
    root=out/'hourly'
    if not root.exists(): return False
    cutoff=datetime.now(timezone.utc)-timedelta(days=HOURLY_RETENTION_DAYS)
    cutoff_month=cutoff.strftime('%Y-%m')
    cutoff_stamp=cutoff.isoformat().replace('+00:00','Z')
    removed=False
    for path in root.rglob('*.jsonl'):
        if path.stem < cutoff_month:
            try:
                path.unlink(); removed=True
            except OSError:
                pass
        elif path.stem == cutoff_month:
            try:
                lines=path.read_text(encoding='utf-8').splitlines()
                kept=[]
                for line in lines:
                    if not line.strip(): continue
                    try:
                        record=json.loads(line)
                        at=str(record.get('at',''))
                        if at >= cutoff_stamp: kept.append(line)
                    except json.JSONDecodeError:
                        continue
                if len(kept) != len([line for line in lines if line.strip()]):
                    if kept:
                        tmp=path.with_suffix(path.suffix+'.tmp')
                        tmp.write_text('\n'.join(kept)+'\n',encoding='utf-8')
                        tmp.replace(path)
                    else:
                        path.unlink()
                    removed=True
            except OSError:
                pass
    if removed:
        for folder in sorted((p for p in root.rglob('*') if p.is_dir()),key=lambda x:len(x.parts),reverse=True):
            try: folder.rmdir()
            except OSError: pass
    return removed

def directory_links(url: str) -> list[str]:
    """Return links from one public asset-browser directory listing."""
    raw=fetch(url).decode('utf-8','replace')
    parser=LinkParser(); parser.feed(raw)
    links=parser.hrefs + re.findall(r'''(?:href\s*=\s*["']?)([^\s"'>]+)''', raw, flags=re.I)
    found=[]
    for href in links:
        full=urljoin(url,href.split('?',1)[0])
        if full.startswith(('http://','https://')) and full not in found:
            found.append(full)
    return found


def list_archive_manifests(full_backfill: bool, processed_dates: set[str]) -> list[tuple[str,str]]:
    """Discover dated manifests. Normal hourly runs inspect only the newest archive month."""
    root_links=directory_links(ARCHIVE_ROOT)
    years=[]
    for link in root_links:
        match=re.search(r'/archive/(\d{4})/?$', link)
        if match: years.append((int(match.group(1)),link.rstrip('/')+'/'))
    years.sort(reverse=True)
    found: dict[str,str]={}
    months_checked=0
    for _,year_url in years:
        try: year_links=directory_links(year_url)
        except Exception as exc:
            print(f'   archive directory skipped: {year_url}: {exc}',file=sys.stderr); continue
        months=[]
        for link in year_links:
            match=re.search(r'/archive/\d{4}/(\d{2})/?$',link)
            if match: months.append((int(match.group(1)),link.rstrip('/')+'/'))
        months.sort(reverse=True)
        for _,month_url in months:
            try: links=directory_links(month_url)
            except Exception as exc:
                print(f'   archive month skipped: {month_url}: {exc}',file=sys.stderr); continue
            month_manifests=[]
            for link in links:
                filename=link.split('?',1)[0].rstrip('/').rsplit('/',1)[-1]
                match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})\.tar\.zst\.manifest\.json',filename,re.I)
                if match: month_manifests.append((match.group(1),link))
            month_manifests.sort(key=lambda x:x[0])
            for date,url in month_manifests:
                found[date]=url
            months_checked += 1
            if not full_backfill and month_manifests:
                # Archives are immutable once published. Recheck the newest 12 dates to
                # recover from a failed/partial run without crawling years of listings hourly.
                newest=month_manifests[-ARCHIVE_RECENT_MANIFESTS:]
                return sorted(newest,key=lambda x:x[0])
    return sorted(found.items(),key=lambda x:x[0])


def archive_date_from_url(url: str) -> str | None:
    filename=url.split('?',1)[0].rstrip('/').rsplit('/',1)[-1]
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2})\.tar\.zst\.manifest\.json',filename,re.I)
    return match.group(1) if match else None


def append_jsonl_once(path: Path, date: str, record: dict[str,Any]) -> bool:
    """Append one date record per file, with a small per-run index for restart safety."""
    cache_key=str(path.resolve())
    if cache_key not in _JSONL_DATE_CACHE:
        dates=set()
        if path.exists():
            try:
                with path.open('rb') as fh:
                    for raw in fh:
                        # Only inspect the fixed JSON prefix; don't parse large ranking rows.
                        match=re.match(br'^\s*\{"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"',raw)
                        if match: dates.add(match.group(1).decode('ascii'))
            except OSError:
                pass
        _JSONL_DATE_CACHE[cache_key]=dates
    dates=_JSONL_DATE_CACHE[cache_key]
    if date in dates: return False
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8',newline='\n') as fh:
        fh.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
    dates.add(date)
    return True


def archive_month_index(out: Path, processed_dates: set[str], latest_day: str | None) -> dict[str,Any]:
    seasons: dict[str,dict[str,list[str]]]={}
    root=out/'archive-history'
    if root.exists():
        for path in root.glob('p-*/*/*.jsonl'):
            season=path.parent.parent.name; rule=path.parent.name
            month_match=re.fullmatch(r'(\d{4}-\d{2})(?:-s\d{2})?',path.stem)
            if not month_match: continue
            month=month_match.group(1)
            seasons.setdefault(season,{}).setdefault(rule,[]).append(month)
        for rules in seasons.values():
            for rule in rules:
                rules[rule]=sorted(set(rules[rule]))
    old=read_json(out/'archive-index.json') or {}
    body={'scope':'P-season X Match rankings only','history_format_version':ARCHIVE_HISTORY_FORMAT_VERSION,'days_processed':len(processed_dates),
          'latest_archive_day':latest_day,'seasons':seasons}
    old_body={k:v for k,v in old.items() if k!='generated_at'}
    generated_at=old.get('generated_at') if old_body==body else None
    return {'generated_at':generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z'),**body}


def _valid_power(value: Any) -> float | None:
    try:
        result = float(value)
        if result != result or result in (float('inf'), float('-inf')):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _merge_archive_best(players: dict[str, Any], key: str, season: str, rule: str,
                        at: str | None, name: str, rank: Any, power: Any,
                        weapon: Any = None, name_id: Any = None) -> bool:
    """Keep the highest observed XP for each player/rule from timestamped archives."""
    score = _valid_power(power)
    if not key or rule not in RULES or score is None:
        return False
    player = players.setdefault(str(key), {'key': str(key), 'rules': {}})
    previous = player['rules'].get(rule)
    replace = previous is None or score > float(previous.get('x_power', -float('inf')))
    if previous and score == float(previous.get('x_power', -float('inf'))):
        old_at = str(previous.get('at') or '')
        new_at = str(at or '')
        # On equal personal-best XP, retain the earliest known achievement time.
        replace = bool(new_at and (not old_at or new_at < old_at))
    if not replace:
        return False
    player['rules'][rule] = {
        'player_key': str(key), 'name_at_record': str(name or ''),
        'name_id': str(name_id or ''), 'rank': rank, 'x_power': score,
        'weapon': str(weapon or ''), 'season': str(season), 'rule': str(rule),
        'at': at or None, 'source': 'archive'
    }
    return True


def _archive_max_from_existing_history(out: Path) -> dict[str, Any]:
    """One-time rebuild of the all-time XP cache from already imported archive shards."""
    players: dict[str, Any] = {}
    root = out / 'archive-history'
    if root.exists():
        for path in sorted(root.glob('p-*/*/*.jsonl')):
            season = path.parent.parent.name
            rule = path.parent.name
            if rule not in RULES:
                continue
            try:
                with path.open('r', encoding='utf-8') as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try: record = json.loads(line)
                        except json.JSONDecodeError: continue
                        for ev in record.get('events', []):
                            # event columns: at, player_key, name, rank, x_power, weapon, name_id
                            if not isinstance(ev, list) or len(ev) < 6:
                                continue
                            at = str(ev[0] or '') or None
                            key = str(ev[1] or '')
                            name = str(ev[2] or '')
                            rank = ev[3]
                            power = ev[4]
                            weapon = ev[5]
                            name_id = ev[6] if len(ev) > 6 else ''
                            ev_season = str(record.get('season') or season)
                            ev_rule = str(record.get('rule') or rule)
                            _merge_archive_best(players, key, ev_season, ev_rule, at,
                                                name, rank, power, weapon, name_id)
            except OSError as exc:
                print(f'   警告: 全期間ランキングの履歴読込に失敗: {path}: {exc}', file=sys.stderr)
    return {'version': ALL_TIME_RANKING_VERSION, 'players': players}


def ensure_archive_max_cache(out: Path) -> None:
    """Load the archive high-water index, rebuilding it once if missing or outdated."""
    global _ACTIVE_ARCHIVE_MAX
    path = out / 'all-time-archive-max.json'
    cached = read_json(path)
    if isinstance(cached, dict) and cached.get('version') == ALL_TIME_RANKING_VERSION and isinstance(cached.get('players'), dict):
        _ACTIVE_ARCHIVE_MAX = cached
    else:
        print('   全期間ランキングの最高XP索引を初期構築します（既存アーカイブを走査）。')
        _ACTIVE_ARCHIVE_MAX = _archive_max_from_existing_history(out)


def update_archive_max_from_snapshots(snapshots: list[tuple[str, str, str, list[list[Any]]]]) -> None:
    """Incrementally include all snapshots from a newly downloaded daily archive."""
    if not isinstance(_ACTIVE_ARCHIVE_MAX, dict):
        return
    players = _ACTIVE_ARCHIVE_MAX.setdefault('players', {})
    for season, rule, at, rows in snapshots:
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            key, name, rank, power = row[:4]
            weapon = row[4] if len(row) > 4 else ''
            name_id = row[5] if len(row) > 5 else ''
            _merge_archive_best(players, str(key or ''), season, rule, at,
                                str(name or ''), rank, power, weapon, name_id)


def save_archive_max_cache(out: Path) -> bool:
    """Persist the incremental cache to the repository, but do not publish it to Pages."""
    if not isinstance(_ACTIVE_ARCHIVE_MAX, dict):
        return False
    path = out / 'all-time-archive-max.json'
    if read_json(path) == _ACTIVE_ARCHIVE_MAX:
        return False
    write_json(path, _ACTIVE_ARCHIVE_MAX, compact=True)
    return True


def build_all_time_rankings(out: Path, archive_summary: dict[str, Any] | None = None) -> bool:
    """Publish top-500 personal bests per rule and a deduplicated top-1000 overall list."""
    cache = _ACTIVE_ARCHIVE_MAX if isinstance(_ACTIVE_ARCHIVE_MAX, dict) else (read_json(out/'all-time-archive-max.json') or {})
    best_by_rule: dict[str, dict[str, dict[str, Any]]] = {rule: {} for rule in RULES}

    # Timestamped maximums recovered from the daily compressed archive snapshots.
    for key, person in (cache.get('players') or {}).items():
        for rule, record in (person.get('rules') or {}).items():
            if rule in best_by_rule and _valid_power(record.get('x_power')) is not None:
                best_by_rule[rule][str(key)] = dict(record, player_key=str(key), rule=rule)

    # Add season endpoint rankings as a fallback and ensure every published season is included.
    seasons_root = out / 'seasons'
    if seasons_root.exists():
        season_files = sorted(seasons_root.glob('p-*/*.json'), key=lambda p: (sort_key(p.parent.name), p.stem))
        for path in season_files:
            season, rule = path.parent.name, path.stem
            if rule not in best_by_rule:
                continue
            payload = read_json(path) or {}
            for player in payload.get('players', []):
                key = str(player.get('player_key') or player.get('id') or '')
                score = _valid_power(player.get('x_power'))
                if not key or score is None:
                    continue
                old = best_by_rule[rule].get(key)
                if old is not None and score <= float(old.get('x_power', -float('inf'))):
                    continue
                best_by_rule[rule][key] = {
                    'player_key': key, 'name_at_record': str(player.get('name') or ''),
                    'name_id': str(player.get('name_id') or ''), 'rank': player.get('rank'),
                    'x_power': score, 'weapon': str(player.get('weapon') or ''),
                    'season': season, 'rule': rule, 'at': None,
                    'source': 'season-endpoint'
                }

    names_index = read_json(out/'player-names-index.json') or {}
    name_players = names_index.get('players') or {}

    def public_row(key: str, record: dict[str, Any]) -> dict[str, Any]:
        name_meta = name_players.get(key) or {}
        # Prefer the latest observed display name; retain the name at the record for context.
        display_name = str(name_meta.get('name') or record.get('name_at_record') or '名前不明')
        return {
            'player_key': key, 'name': display_name,
            'name_at_record': str(record.get('name_at_record') or display_name),
            'name_id': str(record.get('name_id') or name_meta.get('name_id') or ''),
            'x_power': record.get('x_power'), 'rank_at_record': record.get('rank'),
            'weapon': str(record.get('weapon') or ''), 'season': str(record.get('season') or ''),
            'rule': str(record.get('rule') or ''), 'at': record.get('at'),
            'source': str(record.get('source') or 'archive')
        }

    def sort_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # XP descending; ties use better rank-at-record, then stable player key.
        return sorted(records, key=lambda x: (-float(x.get('x_power') or 0),
                                               float(x.get('rank_at_record') or 10**9),
                                               str(x.get('player_key') or '')))

    rule_output = {}
    for rule in RULES:
        all_rows = sort_records([public_row(key, rec) for key, rec in best_by_rule[rule].items()])
        rule_output[rule] = {'total_players': len(all_rows), 'limit': 500, 'players': all_rows[:500]}

    # Overall means each player appears once, using their highest single-rule personal best.
    overall_by_player: dict[str, dict[str, Any]] = {}
    for rule in RULES:
        for key, record in best_by_rule[rule].items():
            previous = overall_by_player.get(key)
            score = float(record.get('x_power') or 0)
            previous_score = float(previous.get('x_power') or 0) if previous else -float('inf')
            if previous is None or score > previous_score:
                overall_by_player[key] = dict(record, player_key=key, rule=rule)
            elif previous is not None and score == previous_score:
                # Equal XP across rules: keep the better recorded rank, if one is known.
                r1 = float(record.get('rank') or 10**9)
                r2 = float(previous.get('rank') or 10**9)
                if r1 < r2:
                    overall_by_player[key] = dict(record, player_key=key, rule=rule)
    overall_rows = sort_records([public_row(key, rec) for key, rec in overall_by_player.items()])

    state = read_json(out/'archive-state.json') or {}
    candidate = {
        'version': ALL_TIME_RANKING_VERSION,
        'scope': 'P-season X Match rankings only; personal best means maximum observed xPower per player and rule across all available season endpoints and timestamped archive snapshots',
        'generated_at': None,
        'archive_backfill_complete': bool(state.get('backfill_complete')),
        'archive_days_processed': len(state.get('processed_dates') or []),
        'rules': rule_output,
        'overall': {'total_players': len(overall_rows), 'limit': 1000, 'players': overall_rows[:1000]}
    }
    target = out/'all-time-ranking.json'
    old = read_json(target) or {}
    old_core = {k:v for k,v in old.items() if k != 'generated_at'}
    new_core = {k:v for k,v in candidate.items() if k != 'generated_at'}
    if old_core == new_core:
        return False
    candidate['generated_at'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
    write_json(target, candidate, compact=True)
    return True


def process_archive_manifest(manifest_url: str, out: Path) -> tuple[str,bool]:
    """Extract P-season rankings from one daily archive and store per-player deltas.

    The raw daily archive is compressed and contains many non-ranking files. Only P-season
    X Match ranking JSONs are parsed. Instead of writing a whole leaderboard for every
    timestamp, store the first state and then only player rows that changed; this keeps the
    long-running GitHub Pages history much smaller while retaining player-level tracking.
    """
    if zstd is None:
        raise RuntimeError('zstandard is missing. Install dependencies from requirements.txt first.')
    date=archive_date_from_url(manifest_url)
    if not date: raise ValueError(f'Unexpected archive manifest URL: {manifest_url}')
    manifest=json.loads(fetch(manifest_url).decode('utf-8-sig'))
    archive_path=str((manifest.get('archive') or {}).get('path') or '')
    if not archive_path: raise ValueError(f'No archive.path in manifest for {date}')
    relevant={}
    for item in manifest.get('files',[]):
        path=str(item.get('path') or '').replace('\\','/')
        filename=path.rsplit('/',1)[-1]
        match=ARCHIVE_FILE_RE.fullmatch(filename)
        if not match: continue
        if int(item.get('bytes') or 0) < MIN_ARCHIVE_RANKING_BYTES: continue
        relevant[filename]=(match.group('season').lower(),match.group('rule').lower(),match.group('date'),match.group('time'))
    raw_snapshots: list[tuple[str,str,str,list[list[Any]]]]=[]
    name_events: dict[tuple[str,str,str,str],dict[str,Any]]={}
    if relevant:
        blob=fetch(urljoin(ARCHIVE_CDN,archive_path),timeout=120)
        dctx=zstd.ZstdDecompressor()
        with dctx.stream_reader(io.BytesIO(blob)) as reader:
            with tarfile.open(fileobj=reader,mode='r|') as tf:
                for member in tf:
                    if not member.isfile(): continue
                    filename=member.name.replace('\\','/').rsplit('/',1)[-1]
                    meta=relevant.get(filename)
                    if not meta: continue
                    season,rule,file_date,file_time=meta
                    extracted=tf.extractfile(member)
                    if extracted is None: continue
                    try: payload=json.loads(extracted.read().decode('utf-8-sig'))
                    except (UnicodeDecodeError,json.JSONDecodeError): continue
                    players=nodes(payload)
                    if not players: continue
                    at=f'{file_date}T{file_time.replace("-",":")}Z'
                    compact=[hourly_player(p) for p in players]
                    compact.sort(key=lambda p:str(p[0] or ''))
                    raw_snapshots.append((season,rule,at,compact))
                    for p in players:
                        pkey=str(p.get('player_key') or '')
                        pname=str(p.get('name') or '').strip()
                        if not pkey or not pname: continue
                        nk=(pkey,pname,season,rule)
                        if nk not in name_events:
                            name_events[nk]={'key':pkey,'name':pname,'first_at':at,'last_at':at,'season':season,'rule':rule}
                        else:
                            name_events[nk]['first_at']=min(name_events[nk]['first_at'],at)
                            name_events[nk]['last_at']=max(name_events[nk]['last_at'],at)
    # Update all-time XP records from every snapshot contained in this daily archive.
    update_archive_max_from_snapshots(raw_snapshots)

    # Derive player-level events in timestamp order so the first record is a full baseline,
    # and later rows only capture a changed rank, XP, weapon, or name.
    raw_snapshots.sort(key=lambda row:(row[0],row[1],row[2]))
    history_events: dict[tuple[str,str],list[list[Any]]]={}
    previous_state: dict[tuple[str,str],dict[str,list[Any]]]={}
    for season,rule,at,players in raw_snapshots:
        key=(season,rule)
        current={str(row[0]):row for row in players if row and row[0]}
        previous=previous_state.get(key)
        target=history_events.setdefault(key,[])
        if previous is None:
            target.extend([[at,*row] for row in players])
        else:
            for player_key,row in current.items():
                if previous.get(player_key)!=row:
                    target.append([at,*row])
        previous_state[key]=current
    month=date[:7]
    changed=False
    for (season,rule),events in sorted(history_events.items()):
        events.sort(key=lambda event:(event[0],str(event[1])))
        # Keep a single player's history in just one of 32 files. On mobile this means
        # opening a profile downloads a small shard instead of scanning the full leaderboard archive.
        shard_events: dict[int,list[list[Any]]]={}
        for event in events:
            key=str(event[1] or '') if len(event)>1 else ''
            shard=player_history_shard(key)
            shard_events.setdefault(shard,[]).append(event)
        for shard, rows in sorted(shard_events.items()):
            history_path=out/'archive-history'/season/rule/f'{month}-s{shard:02d}.jsonl'
            record={'date':date,'season':season,'rule':rule,'events':rows}
            if append_jsonl_once(history_path,date,record): changed=True
    name_path=out/'archive-name-events'/f'{month}.jsonl'
    events=[]
    for value in sorted(name_events.values(),key=lambda e:(e['key'],e['name'],e['season'],e['rule'])):
        events.append([value['key'],value['name'],value['first_at'],value['last_at'],value['season'],value['rule']])
    if append_jsonl_once(name_path,date,{'date':date,'events':events}): changed=True
    return date,changed


def observe_name(index: dict[str,Any], key: str, name: str, at: str|None,
                 season: str, rule: str, month: str|None, archive_observation: bool) -> None:
    if not key or not name: return
    player=index.setdefault(key,{'key':key,'name':name,'name_id':'','names':{},'history_months':{}})
    alias=player['names'].setdefault(name,{'name':name,'first_seen':at,'last_seen':at,'seasons':[],'rules':[]})
    if at:
        if not alias.get('first_seen') or at < alias['first_seen']: alias['first_seen']=at
        if not alias.get('last_seen') or at > alias['last_seen']: alias['last_seen']=at
    if season and season not in alias['seasons']: alias['seasons'].append(season)
    if rule and rule not in alias['rules']: alias['rules'].append(rule)
    if archive_observation and month and season and rule:
        player['history_months'].setdefault(season,{}).setdefault(rule,[])
        if month not in player['history_months'][season][rule]: player['history_months'][season][rule].append(month)
    if at and (not player.get('_latest_at') or at >= player['_latest_at']):
        player['name']=name; player['_latest_at']=at


def rebuild_player_name_index(out: Path) -> None:
    """Build a site-friendly player index with aliases and the archive files to query."""
    players: dict[str,Any]={}
    # Historical names are captured from every valid P-season ranking snapshot in the
    # source archive, not just the one daily snapshot retained for trend charts.
    event_root=out/'archive-name-events'
    if event_root.exists():
        for path in sorted(event_root.glob('*.jsonl')):
            month=path.stem
            with path.open('r',encoding='utf-8') as fh:
                for line in fh:
                    if not line.strip(): continue
                    try: record=json.loads(line)
                    except json.JSONDecodeError: continue
                    for ev in record.get('events',[]):
                        if len(ev)<6: continue
                        key,name,first_at,last_at,season,rule=ev[:6]
                        observe_name(players,str(key),str(name),last_at,str(season),str(rule),month,True)
                        # Keep both ends of the observed interval for a name.
                        alias=players[str(key)]['names'][str(name)]
                        if first_at and (not alias.get('first_seen') or first_at<alias['first_seen']): alias['first_seen']=first_at
                        if last_at and (not alias.get('last_seen') or last_at>alias['last_seen']): alias['last_seen']=last_at
    # Include season endpoint names too, so search works even before archive backfill.
    seasons_root=out/'seasons'
    if seasons_root.exists():
        season_files=sorted(seasons_root.glob('p-*/*.json'),key=lambda p:(sort_key(p.parent.name),p.stem))
        latest_static_season=max((p.parent.name for p in season_files),key=sort_key,default='')
        for path in season_files:
            season=path.parent.name; rule=path.stem; payload=read_json(path) or {}
            for p in payload.get('players',[]):
                key=str(p.get('player_key') or p.get('id') or '')
                name=str(p.get('name') or '').strip()
                observe_name(players,key,name,None,season,rule,None,False)
                if p.get('name_id') and not players[key].get('name_id'): players[key]['name_id']=str(p['name_id'])
                # The most recent season endpoint is the best available fallback when
                # this player has no timestamped archive observation.
                if season==latest_static_season and not players[key].get('_latest_at') and name:
                    players[key]['name']=name
    output={}
    for key,player in players.items():
        names=list(player.get('names',{}).values())
        names.sort(key=lambda a:(a.get('first_seen') or '9999',a.get('name') or ''))
        for alias in names:
            alias['seasons']=sorted(set(alias.get('seasons',[])),key=sort_key)
            alias['rules']=sorted(set(alias.get('rules',[])))
        hm={}
        for season,rules in player.get('history_months',{}).items():
            hm[season]={rule:sorted(set(months)) for rule,months in rules.items()}
        best_name=player.get('name') or (names[-1]['name'] if names else '名前不明')
        output[key]={'key':key,'name':best_name,'name_id':player.get('name_id',''),'names':names,'history_months':hm}
    write_json(out/'player-names-index.json',{'generated_at':datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z'),'players':output},compact=True)


def process_archives(out: Path, full_backfill: bool = False) -> tuple[bool,dict[str,Any]]:
    state_path=out/'archive-state.json'
    state_exists=state_path.exists()
    state=read_json(state_path) or {'processed_dates':[]}
    if state_exists and int(state.get('history_format_version',0) or 0) != ARCHIVE_HISTORY_FORMAT_VERSION:
        # Older deployments used one large JSONL file per month. Reset just the derived
        # history indexes and reimport archives into sharded files so old state cannot
        # accidentally mark months complete while the new frontend expects shards.
        print('   履歴フォーマットを更新するため、過去アーカイブの索引を作り直します。')
        for directory in (out/'archive-history', out/'archive-name-events'):
            if directory.exists(): shutil.rmtree(directory)
        for file in (out/'archive-index.json', out/'player-names-index.json'):
            try: file.unlink()
            except FileNotFoundError: pass
        state={'processed_dates':[],'backfill_started':True,'backfill_complete':False,
               'history_format_version':ARCHIVE_HISTORY_FORMAT_VERSION}
        write_json(state_path,state)
    processed=set(str(x) for x in state.get('processed_dates',[]))
    # The first run automatically starts a resumable full backfill. Every run processes
    # archives until the time budget is reached, then checkpoints so the next run resumes.
    backfill_started=bool(state.get('backfill_started')) or 'backfill_started' not in state or not state_path.exists() or full_backfill
    backfill_complete=bool(state.get('backfill_complete')) and not full_backfill
    backfill_mode=backfill_started and not backfill_complete
    all_manifests: list[tuple[str,str]]=[]
    failures=[]; changed=False
    if backfill_mode:
        catalog_path=out/'archive-manifest-catalog.json'
        catalog=read_json(catalog_path) or {}
        today=datetime.now(timezone.utc).date().isoformat()
        catalog_complete=bool(catalog.get('full_catalog_complete'))
        need_catalog_refresh=(not catalog.get('manifests') or not catalog_complete or str(catalog.get('generated_date','')) < today)
        if need_catalog_refresh:
            try:
                discovered=list_archive_manifests(True,processed)
                if discovered:
                    all_manifests=discovered
                    catalog_complete=True
                    catalog={'generated_date':today,'full_catalog_complete':True,'manifests':[[date,url] for date,url in all_manifests]}
                    write_json(catalog_path,catalog,compact=True)
                else:
                    all_manifests=[]
                    failures.append('full archive listing returned no dated manifests')
                    catalog_complete=False
            except Exception as exc:
                failures.append(f'full archive listing: {exc}')
                all_manifests=[]
                catalog_complete=False
        else:
            all_manifests=[(str(date),str(url)) for date,url in catalog.get('manifests',[]) if isinstance(date,str) and isinstance(url,str)]
        # Add the newest manifest links on each run, in case a daily archive was published
        # after the slower full catalog refresh.
        try:
            recent=list_archive_manifests(False,processed)
        except Exception as exc:
            recent=[]; failures.append(f'recent archive listing: {exc}')
        catalog_map={date:url for date,url in all_manifests}
        for date,url in recent:
            catalog_map[date]=url
        all_manifests=sorted(catalog_map.items(),key=lambda x:x[0])
        if all_manifests:
            write_json(catalog_path,{'generated_date':str(catalog.get('generated_date') or ''),
                                     'full_catalog_complete':catalog_complete,
                                     'manifests':[[date,url] for date,url in all_manifests]},compact=True)
        pending=[item for item in all_manifests if item[0] not in processed]
        # Prioritize the newest published archive dates first, so recent name/XP changes
        # become visible promptly. Then continue from the oldest unprocessed archive.
        recent_pending=sorted(
            ((date,url) for date,url in recent if date not in processed),
            key=lambda x:x[0], reverse=True
        )
        recent_dates={date for date,_ in recent_pending}
        historical_pending=sorted(
            (item for item in pending if item[0] not in recent_dates),
            key=lambda x:x[0]
        )
        urls=recent_pending + historical_pending
        state.update({'backfill_started':True,'backfill_complete':False,'history_format_version':ARCHIVE_HISTORY_FORMAT_VERSION})
        write_json(state_path,state)
        print(f'   過去アーカイブの全件取り込み中: 今回は最大{ARCHIVE_BACKFILL_MAX_SECONDS // 60}分間、最新分を優先してから古い日付へ遡ります。')
    else:
        try: urls=list_archive_manifests(False,processed)
        except Exception as exc:
            urls=[]; failures.append(f'archive listing: {exc}')
    if not urls:
        print('   過去アーカイブ: 新たに処理するマニフェストがありませんでした。')
    backfill_started_at=time.monotonic()
    for i,(listed_date,url) in enumerate(urls,1):
        if listed_date in processed: continue
        if backfill_mode and time.monotonic()-backfill_started_at >= ARCHIVE_BACKFILL_MAX_SECONDS:
            print(f'   時間予算（{ARCHIVE_BACKFILL_MAX_SECONDS // 60}分）に達したため、進捗を保存して次回へ引き継ぎます。')
            break
        try:
            date,day_changed=process_archive_manifest(url,out)
            processed.add(date); changed=changed or day_changed
            state={'processed_dates':sorted(processed),
                   'latest_archive_day':max(processed) if processed else None,
                   'backfill_started':backfill_started,
                   'backfill_complete':False if backfill_mode else bool(state.get('backfill_complete',False)),
                   'history_format_version':ARCHIVE_HISTORY_FORMAT_VERSION}
            write_json(state_path,state)
            print(f'   archive {i}/{len(urls)}: {date} を処理（Pランキング履歴）')
        except (HTTPError,URLError,TimeoutError,OSError,ValueError,RuntimeError,tarfile.TarError) as exc:
            message=f'archive {listed_date}: {exc}'
            failures.append(message); print(f'   警告 {message}',file=sys.stderr)
    if backfill_mode:
        # A full directory listing lets the next hourly run resume from the oldest
        # remaining date. Recent dates may be processed in parallel with the backfill.
        remaining=[date for date,_ in all_manifests if date not in processed]
        complete=catalog_complete and bool(all_manifests) and not remaining and not any('archive ' in f for f in failures)
        state={'processed_dates':sorted(processed),
               'latest_archive_day':max(processed) if processed else None,
               'backfill_started':True,'backfill_complete':complete,
               'backfill_remaining_days':len(remaining),'history_format_version':ARCHIVE_HISTORY_FORMAT_VERSION}
        write_json(state_path,state)
        if complete:
            print('   過去アーカイブの初回取り込みが完了しました。')
        else:
            print(f'   過去アーカイブの初回取り込みは継続中です（未処理日数の目安: {len(remaining)}）。次回の定期実行で続きから再開します。')
    archive_idx=archive_month_index(out,processed,max(processed) if processed else None)
    idx_path=out/'archive-index.json'
    if read_json(idx_path)!=archive_idx:
        write_json(idx_path,archive_idx); changed=True
    final_state=read_json(state_path) or {}
    final_catalog=read_json(out/'archive-manifest-catalog.json') or {}
    catalog_manifests=final_catalog.get('manifests',[])
    total_days=len(catalog_manifests) if final_catalog.get('full_catalog_complete') and isinstance(catalog_manifests,list) else None
    remaining_days=final_state.get('backfill_remaining_days')
    if remaining_days is None and total_days is not None:
        catalog_dates={str(item[0]) for item in catalog_manifests if isinstance(item,list) and item}
        remaining_days=len(catalog_dates-processed)
    return changed,{'processed':len(processed),'latest':max(processed) if processed else None,'failures':failures,
                    'backfill_started':bool(final_state.get('backfill_started')),'backfill_complete':bool(final_state.get('backfill_complete')),'total':total_days,'remaining':remaining_days}


def update(out: Path, refresh_history: bool = False, backfill_archive: bool = False):
    seasons=get_listing()
    ordered=sorted(seasons,key=sort_key,reverse=True)
    latest=ordered[0]
    print(f'検出したシーズン: {len(ordered)} / 最新: {latest}')
    previous_index=read_json(out/'index.json') or {}
    index_seasons=[]
    failures=[]
    changed=False
    season_data_changed=False
    latest_snapshot_at=None
    for si, season in enumerate(ordered,1):
        rules_meta={}
        season_dir=out/'seasons'/season
        print(f'[{si}/{len(ordered)}] {season}')
        for rule in RULES:
            url=seasons[season][rule]
            target=season_dir/f'{rule}.json'
            should_fetch = season == latest or refresh_history or not valid_cached(target)
            if should_fetch:
                try:
                    payload=json.loads(fetch(url).decode('utf-8-sig'))
                    players=nodes(payload)
                    if not players: raise ValueError('ランキングプレイヤーが0件でした（JSON形式を確認してください）')
                    existing=read_json(target)
                    if not isinstance(existing,dict) or stable_players(existing.get('players',[])) != stable_players(players):
                        stamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
                        write_json(target,{'season':season,'rule':rule,'rule_ja':RULE_JA[rule],'retrieved_at':stamp,'source_url':url,'players':players},compact=True)
                        changed=True
                        season_data_changed=True
                        print(f'   {RULE_JA[rule]}: {len(players)}人取得（変化あり）')
                    else:
                        print(f'   {RULE_JA[rule]}: {len(players)}人取得（変化なし）')
                    if season == latest:
                        added,stamp=append_hourly_snapshot(out,season,rule,players)
                        if added:
                            changed=True
                            print(f'   └ 時系列スナップショットを追加: {stamp}')
                        if stamp and (latest_snapshot_at is None or stamp > latest_snapshot_at):
                            latest_snapshot_at=stamp
                except (HTTPError,URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
                    failures.append(f'{season}/{rule}: {exc}')
                    print(f'   警告 {rule}: {exc}',file=sys.stderr)
            try:
                saved=json.loads(target.read_text(encoding='utf-8'))
                count=len(saved.get('players',[]))
                rules_meta[rule]={'url':f'data/seasons/{season}/{rule}.json','count':count,'source_url':url}
            except Exception:
                rules_meta[rule]={'url':f'data/seasons/{season}/{rule}.json','count':0,'source_url':url}
            time.sleep(0.025)
        index_seasons.append({'key':season,'label':season.upper(),'rules':rules_meta})
    # Load or rebuild the all-time max cache before ingesting new archives so every new
    # daily manifest can update it incrementally without rescanning the full history.
    ensure_archive_max_cache(out)
    archive_changed, archive_summary = process_archives(out, full_backfill=backfill_archive)
    changed = changed or archive_changed
    archive_max_changed = save_archive_max_cache(out)
    changed = changed or archive_max_changed
    if archive_changed or season_data_changed or not (out/'player-names-index.json').exists():
        rebuild_player_name_index(out)
    all_time_changed = build_all_time_rankings(out, archive_summary)
    changed = changed or all_time_changed
    old_hourly=read_json(out/'hourly-index.json') or {}
    if prune_hourly_history(out):
        changed=True
        print(f'時系列履歴を直近{HOURLY_RETENTION_DAYS}日分に整理しました。')
    newest_snapshot_at = latest_snapshot_at or old_hourly.get('latest_snapshot_at')
    build_hourly_index(out,latest,newest_snapshot_at)
    update_heartbeat(out)
    generated_at=previous_index.get('generated_at')
    if changed or not generated_at:
        generated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
    index={'generated_at':generated_at,'source':'https://assets.splatoon3.ink/data/xrank/','scope':'X Match rankings only; no Splatfest, gear, or schedule data','latest_season':latest,'seasons':index_seasons,'archive_days_processed':archive_summary.get('processed',0),'archive_days_total':archive_summary.get('total'),'archive_days_remaining':archive_summary.get('remaining'),'archive_backfill_started':archive_summary.get('backfill_started',False),'archive_backfill_complete':archive_summary.get('backfill_complete',False),'archive_latest_day':archive_summary.get('latest'),'failures':failures + archive_summary.get('failures',[])}
    if read_json(out/'index.json') != index:
        write_json(out/'index.json',index)
        changed=True
    good=sum(1 for s in index_seasons for r in s['rules'].values() if r['count']>0)
    print(f'\n完了: {len(index_seasons)}シーズン / {good}ランキングJSONが利用可能')
    print(f'保存先: {out.resolve()}')
    print(f'直近{HOURLY_RETENTION_DAYS}日分の時間別履歴を保持します。')
    print(f"公開アーカイブ: {archive_summary.get('processed',0)}日分を確認済み / 最新日 {archive_summary.get('latest') or '未取得'}")
    print('ランキング履歴はアーカイブ内の有効なPシーズンのランキング変化を時刻付きで保存し、過去の名前は全ての有効なPランキングから抽出します。')
    alltime = read_json(out/'all-time-ranking.json') or {}
    print(f"全期間ベスト: 各ルール上位{alltime.get('rules',{}).get('splatzones',{}).get('limit',500)}人 / 総合上位{alltime.get('overall',{}).get('limit',1000)}人を更新しました。")
    print('次回の自動確認は GitHub Actions が毎時実行します（サイトやPCを開いておく必要はありません）。')
    if failures:
        print(f'注意: {len(failures)}件は取得・解析に失敗しました。ネットワークを確認後、再実行してください。')
    elif not changed:
        print('ランキング内容に変化がなかったため、重複する履歴は保存していません。')

def main():
    parser=argparse.ArgumentParser(description='Splatoon 3 Xランキングを過去シーズンまで収集（Xマッチのみ）')
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parent/'data',help='保存先（既定: サイト直下のdata）')
    parser.add_argument('--refresh-history',action='store_true',help='取得済みPシーズンのランキングJSONを全件再取得')
    parser.add_argument('--backfill-archive',action='store_true',help='公開元の全アーカイブ日付を優先して収集。1回最大30分で進捗保存し、再実行で続きから再開')
    args=parser.parse_args()
    try: update(args.out,args.refresh_history,args.backfill_archive); return 0
    except (HTTPError,URLError,TimeoutError,OSError,RuntimeError) as exc:
        print(f'取得に失敗しました: {exc}',file=sys.stderr); return 1
if __name__=='__main__': raise SystemExit(main())
