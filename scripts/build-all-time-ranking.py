#!/usr/bin/env python3
"""Build P-season all-time best XP rankings from archived ranking snapshots.

Identity rule: use ONLY the opaque player_key stored in archive event field 1.
Do not fall back to the four-digit player number, display name, or nameId.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
RULES = ("splatzones", "towercontrol", "rainmaker", "clamblitz")
RULE_PATH_INDEX = {name: i for i, name in enumerate(RULES)}


def parse_time(value: Any, fallback: Any = None) -> tuple[int, float | str]:
    """Return a comparable time key without relying on player names/IDs."""
    for raw in (value, fallback):
        if raw is None or raw == "":
            continue
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            n = float(raw)
            if math.isfinite(n):
                # Handle Unix milliseconds as well as seconds.
                return (2, n / 1000 if n > 10_000_000_000 else n)
        s = str(raw).strip()
        if not s:
            continue
        if s.replace(".", "", 1).isdigit():
            try:
                n = float(s)
                return (2, n / 1000 if n > 10_000_000_000 else n)
            except ValueError:
                pass
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (2, parsed.timestamp())
        except ValueError:
            # Date-like strings and filenames are compared lexically.
            return (1, s)
    return (0, "")


def numeric_rank(value: Any) -> float:
    try:
        n = float(value)
        return n if math.isfinite(n) else math.inf
    except (TypeError, ValueError):
        return math.inf


def candidate_better(new: dict[str, Any], old: dict[str, Any] | None) -> bool:
    if old is None:
        return True
    new_power, old_power = float(new["x_power"]), float(old["x_power"])
    if new_power != old_power:
        return new_power > old_power
    new_rank, old_rank = numeric_rank(new.get("rank_at_record")), numeric_rank(old.get("rank_at_record"))
    if new_rank != old_rank:
        return new_rank < old_rank
    return new["_time_key"] > old["_time_key"]


def event_fields(event: Any) -> dict[str, Any] | None:
    """Normalize one archive event; player_key is deliberately never inferred."""
    if isinstance(event, list) and len(event) >= 6:
        return {
            "at": event[0],
            "player_key": event[1],
            "name": event[2],
            "rank": event[3],
            "x_power": event[4],
            "weapon": event[5],
            "name_id": event[6] if len(event) > 6 else None,
        }
    if isinstance(event, dict):
        # Only explicit persistent identity fields are accepted. There is no fallback
        # to id, discriminator, nameId, or display name.
        key = event.get("player_key")
        if key is None:
            key = event.get("playerKey")
        return {
            "at": event.get("at", event.get("timestamp", event.get("date"))),
            "player_key": key,
            "name": event.get("name", ""),
            "rank": event.get("rank_at_record", event.get("rank")),
            "x_power": event.get("x_power", event.get("xPower")),
            "weapon": event.get("weapon", event.get("weapon_label")),
            "name_id": event.get("name_id", event.get("nameId")),
        }
    return None


def iter_snapshots(path: Path, archive_root: Path) -> Iterable[tuple[dict[str, Any], str, str, str]]:
    rel_parts = path.relative_to(archive_root).parts
    path_season = rel_parts[0] if len(rel_parts) >= 1 else ""
    path_rule = rel_parts[1] if len(rel_parts) >= 2 else ""
    with path.open("r", encoding="utf-8") as f:
        for line_number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                snapshot = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"警告: {path}:{line_number}: JSONを読み飛ばしました ({exc})", file=sys.stderr)
                continue
            if not isinstance(snapshot, dict):
                continue
            season = str(snapshot.get("season") or path_season)
            rule = str(snapshot.get("rule") or path_rule)
            date = str(snapshot.get("date") or "")
            yield snapshot, season, rule, date


def ranking_sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
    return (-float(row["x_power"]), numeric_rank(row.get("rank_at_record")), str(row["player_key"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=ROOT / "data" / "archive-history")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "all-time-ranking.json")
    args = parser.parse_args()
    archive_root = args.archive_root.resolve()
    output = args.output.resolve()

    if not archive_root.is_dir():
        print(f"エラー: アーカイブフォルダがありません: {archive_root}", file=sys.stderr)
        return 2
    files = sorted(p for p in archive_root.rglob("*.jsonl") if p.is_file())
    if not files:
        print(f"エラー: {archive_root} 以下に .jsonl アーカイブがありません", file=sys.stderr)
        return 2

    best_by_rule: dict[tuple[str, str], dict[str, Any]] = {}
    latest_names: dict[str, tuple[tuple[int, float | str], str]] = {}
    valid_rows = 0
    skipped_no_key = 0
    for file in files:
        try:
            snapshots = iter_snapshots(file, archive_root)
            for snapshot, season, rule, date in snapshots:
                if rule not in RULE_PATH_INDEX:
                    continue
                events = snapshot.get("events")
                if not isinstance(events, list):
                    continue
                for raw_event in events:
                    fields = event_fields(raw_event)
                    if not fields:
                        continue
                    raw_key = fields.get("player_key")
                    player_key = str(raw_key).strip() if raw_key is not None else ""
                    # Exact key only: never merge by 4-digit player ID, name, or nameId.
                    if not player_key:
                        skipped_no_key += 1
                        continue
                    try:
                        power = float(fields.get("x_power"))
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(power):
                        continue
                    valid_rows += 1
                    at = fields.get("at")
                    time_key = parse_time(at, date or file.stem)
                    name = str(fields.get("name") or "名前不明")
                    old_name = latest_names.get(player_key)
                    if name and (old_name is None or time_key >= old_name[0]):
                        latest_names[player_key] = (time_key, name)
                    rank = fields.get("rank")
                    candidate = {
                        "player_key": player_key,
                        "name": name,
                        "name_at_record": name,
                        "rank": rank,
                        "rank_at_record": rank,
                        "x_power": power,
                        "weapon": str(fields.get("weapon") or ""),
                        "name_id": str(fields.get("name_id")) if fields.get("name_id") not in (None, "") else None,
                        "rule": rule,
                        "season": season,
                        "at": at,
                        "date": date or (str(at)[:10] if at else ""),
                        "source": "archive",
                        "_time_key": time_key,
                    }
                    key = (player_key, rule)
                    if candidate_better(candidate, best_by_rule.get(key)):
                        best_by_rule[key] = candidate
        except OSError as exc:
            print(f"警告: {file} を読めませんでした: {exc}", file=sys.stderr)

    if valid_rows == 0 or not best_by_rule:
        print("エラー: 有効なプレイヤー識別子付きアーカイブ記録がありません。空のランキングは出力しません。", file=sys.stderr)
        return 3

    def finalize(row: dict[str, Any]) -> dict[str, Any]:
        result = {k: v for k, v in row.items() if not k.startswith("_")}
        result["name"] = latest_names.get(row["player_key"], (None, row.get("name", "名前不明")))[1]
        result["name_at_record"] = row.get("name_at_record", row.get("name", "名前不明"))
        return result

    rule_rows: dict[str, list[dict[str, Any]]] = {rule: [] for rule in RULES}
    for (_, rule), candidate in best_by_rule.items():
        rule_rows[rule].append(finalize(candidate))
    for rule in RULES:
        rule_rows[rule].sort(key=ranking_sort_key)

    overall_candidates: dict[str, dict[str, Any]] = {}
    for candidate in best_by_rule.values():
        old = overall_candidates.get(candidate["player_key"])
        if candidate_better(candidate, old):
            overall_candidates[candidate["player_key"]] = candidate
    overall_rows = [finalize(row) for row in overall_candidates.values()]
    overall_rows.sort(key=ranking_sort_key)

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload = {
        "schema_version": 2,
        "generated_at": generated_at,
        "source": "splatoon3.ink P-season public archive-history",
        "identity_policy": "Exact player_key only; no fallback to 4-digit player ID, name, or nameId.",
        "limits": {"overall": 1500, "per_rule": 500},
        "overall": {
            "total_players": len(overall_rows),
            "players": overall_rows[:1500],
        },
        "rules": {
            rule: {"total_players": len(rule_rows[rule]), "players": rule_rows[rule][:500]}
            for rule in RULES
        },
        "stats": {
            "archive_files": len(files),
            "valid_records": valid_rows,
            "records_skipped_without_player_key": skipped_no_key,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(output)
    print(f"全期間ベストを生成: 総合 {len(overall_rows[:1500])}/{len(overall_rows)} 人、各ルール最大500人")
    print(f"入力: {len(files)} ファイル / {valid_rows:,} 有効記録 / 識別子なし {skipped_no_key:,} 件を除外")
    print(f"出力: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
