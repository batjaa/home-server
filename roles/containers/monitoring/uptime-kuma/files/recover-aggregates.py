#!/usr/bin/env python3
"""Prepare and validate a Kuma aggregate-only repair; never rewrite raw history.

Export reads SQLite snapshots and emits canonical heartbeats for replay by the
same Kuma image. Apply rehearses on a new copy unless --apply is explicit.
For live apply, stop Kuma before the final snapshot/export and keep it stopped
through replay/apply; restart afterward to discard cached statistics.
"""

import argparse
import base64
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3

VERSION = "dev"
TABLES = {"stat_daily": (86400, 365), "stat_hourly": (3600, 720), "stat_minutely": (60, 1440)}


def encoded(value):
    if isinstance(value, bytes):
        return {"bytes": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=encoded, allow_nan=False).encode()


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def readonly(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db, db:
        yield db


def private_output(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def fingerprint(connection):
    """Hash every non-aggregate table, never emit its potentially secret rows."""
    result = {}
    tables = connection.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for name, schema in tables:
        if name.startswith("stat_"):
            continue
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({identifier(name)})")]
        where = " WHERE name NOT LIKE 'stat\\_%' ESCAPE '\\'" if name == "sqlite_sequence" else ""
        order = ",".join(identifier(column) for column in columns)
        digest = hashlib.sha256(json_bytes(schema))
        count = 0
        for row in connection.execute(f"SELECT * FROM {identifier(name)}{where} ORDER BY {order}"):
            digest.update(json_bytes(row) + b"\n")
            count += 1
        result[name] = {"sha256": digest.hexdigest(), "rows": count}
    return result


def utc_timestamp(value):
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.timestamp()


def bucket_key(table, monitor_id, timestamp):
    return f"{table}/{monitor_id}/{timestamp}"


def export(backup, live, output, manifest_path, now):
    now_epoch = int(now) if str(now).isdigit() else utc_timestamp(now)
    cutoffs = {table: math.floor(now_epoch / width) * width - width * keep for table, (width, keep) in TABLES.items()}
    cutoffs["stat_daily"] += 86400
    expected = {}
    digest = hashlib.sha256()
    with readonly(live) as db:
        db.execute("ATTACH DATABASE ? AS original", (Path(backup).resolve().as_uri() + "?mode=ro",))
        db.execute("BEGIN")
        before = fingerprint(db)
        backup_max = db.execute("SELECT coalesce(max(id),0) FROM original.heartbeat").fetchone()[0]
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='heartbeat'").fetchone()
        original_sequence = db.execute("SELECT seq FROM original.sqlite_sequence WHERE name='heartbeat'").fetchone()
        if not sequence or sequence[0] < max(backup_max, original_sequence[0] if original_sequence else 0):
            raise ValueError("Live heartbeat sequence regressed; cannot safely merge by ID")
        conflicts = db.execute("""
            SELECT count(*) FROM main.heartbeat l JOIN original.heartbeat b ON l.id=b.id
            WHERE l.monitor_id IS NOT b.monitor_id OR l.time IS NOT b.time
               OR l.status IS NOT b.status OR l.ping IS NOT b.ping
        """).fetchone()[0]
        if conflicts:
            raise ValueError(f"{conflicts} overlapping heartbeat IDs disagree")
        monitor_ids = {row[0] for row in db.execute("SELECT id FROM main.monitor")}
        sample_count = 0
        delta_count = db.execute("SELECT count(*) FROM main.heartbeat l WHERE NOT EXISTS (SELECT 1 FROM original.heartbeat b WHERE b.id=l.id)").fetchone()[0]
        # A later retry must not erase v2 history whose raw samples have already
        # expired. Require the complete post-backup ID range; even harmless ID
        # gaps fail closed and need manual investigation before rebuilding.
        original_high_water = original_sequence[0] if original_sequence else backup_max
        available_new = db.execute("SELECT count(*) FROM main.heartbeat WHERE id > ?", (original_high_water,)).fetchone()[0]
        if available_new != sequence[0] - original_high_water:
            raise ValueError("Post-backup heartbeat history has gaps; raw samples may have expired")
        query = """
            SELECT id, monitor_id, status, ping, time FROM original.heartbeat
            UNION ALL
            SELECT id, monitor_id, status, ping, time FROM main.heartbeat l
            WHERE NOT EXISTS (SELECT 1 FROM original.heartbeat b WHERE b.id=l.id)
            ORDER BY monitor_id, time, id
        """
        with private_output(output) as stream:
            for _, monitor_id, status, ping, stamp in db.execute(query):
                if monitor_id not in monitor_ids:
                    raise ValueError(f"Historical monitor {monitor_id} no longer exists; resolve explicitly")
                if status not in (0, 1, 2, 3):
                    raise ValueError(f"Unsupported heartbeat status {status}")
                instant = utc_timestamp(stamp)
                if instant >= math.floor(now_epoch) + 1:
                    raise ValueError("Heartbeat is newer than --now; choose a snapshot cutoff after its last sample")
                line = json_bytes([monitor_id, status, ping, stamp]) + b"\n"
                stream.write(line)
                digest.update(line)
                sample_count += 1
                for table, (width, _) in TABLES.items():
                    timestamp = math.floor(instant / width) * width
                    if timestamp < cutoffs[table]:
                        continue
                    key = bucket_key(table, monitor_id, timestamp)
                    stats = expected.setdefault(key, {"up": 0, "down": 0, "maintenance": 0, "ping_complete": True, "ping_sum": 0.0, "ping_min": None, "ping_max": None})
                    if status == 3:
                        stats["maintenance"] += 1
                    elif status in (0, 2):
                        stats["down"] += 1
                    else:
                        stats["up"] += 1
                        try:
                            numeric_ping = float(ping)
                        except (ValueError, TypeError):
                            numeric_ping = math.nan
                        if not math.isfinite(numeric_ping):
                            stats["ping_complete"] = False
                        else:
                            stats["ping_sum"] += numeric_ping
                            stats["ping_min"] = numeric_ping if stats["ping_min"] is None else min(stats["ping_min"], numeric_ping)
                            stats["ping_max"] = numeric_ping if stats["ping_max"] is None else max(stats["ping_max"], numeric_ping)
        for stats in expected.values():
            if stats["ping_complete"]:
                stats["ping"] = stats["ping_sum"] / stats["up"] if stats["up"] else 0
                if not stats["up"]:
                    stats["ping_min"] = stats["ping_max"] = 0
            del stats["ping_sum"]
        manifest = {"format": 1, "now": now, "retention": "daily 365 UTC dates; hour/minute bucket >= floor(now-window)", "live_fingerprint": before, "heartbeat_sha256": digest.hexdigest(), "sample_count": sample_count, "live_delta_count": delta_count, "backup_max_id": backup_max, "live_sequence": sequence[0], "expected": expected}
        with private_output(manifest_path) as stream:
            stream.write(json_bytes(manifest) + b"\n")
    return {"samples": sample_count, "live_delta": delta_count, "aggregate_buckets": len(expected)}


def validate_aggregates(path, manifest):
    expected = manifest["expected"]
    seen = set()
    rows = []
    with open(path) as stream:
        for line in stream:
            row = json.loads(line)
            table = row["table"]
            if table not in TABLES:
                raise ValueError(f"Unexpected table: {table}")
            for field in ("monitor_id", "timestamp", "up", "down"):
                if type(row[field]) is not int or row[field] < 0:
                    raise ValueError(f"Invalid integer {field}")
            key = bucket_key(table, row["monitor_id"], row["timestamp"])
            if key in seen or key not in expected:
                raise ValueError(f"Duplicate or unexpected aggregate bucket: {key}")
            seen.add(key)
            wanted = expected[key]
            extras = row.get("extras") or {}
            if isinstance(extras, str):
                extras = json.loads(extras)
            if not isinstance(extras, dict) or set(extras) - {"maintenance"}:
                raise ValueError(f"Unexpected extras in {key}")
            if type(extras.get("maintenance", 0)) is not int or extras.get("maintenance", 0) < 0:
                raise ValueError(f"Invalid maintenance count in {key}")
            actual_counts = (row["up"], row["down"], extras.get("maintenance", 0))
            wanted_counts = (wanted["up"], wanted["down"], wanted["maintenance"])
            if actual_counts != wanted_counts:
                raise ValueError(f"Sample counts disagree for {key}: {actual_counts} != {wanted_counts}")
            for field in ("ping", "ping_min", "ping_max"):
                if type(row[field]) not in (int, float) or not math.isfinite(row[field]):
                    raise ValueError(f"Non-finite {field} for {key}")
                if wanted["ping_complete"] and not math.isclose(row[field], wanted[field], rel_tol=1e-9, abs_tol=1e-8):
                    raise ValueError(f"{field} disagrees for {key}")
            row["extras"] = json.dumps(extras, separators=(",", ":")) if extras else None
            rows.append(row)
    if seen != set(expected):
        raise ValueError(f"Missing {len(set(expected) - seen)} aggregate buckets")
    return rows


def apply(database, aggregates, manifest_path, output=None, apply_live=False):
    if not Path(database).is_file():
        raise ValueError("The requested database must already exist")
    manifest = json.loads(Path(manifest_path).read_text())
    if manifest.get("format") != 1:
        raise ValueError("Unsupported recovery manifest format")
    rows = validate_aggregates(aggregates, manifest)
    if apply_live:
        if output:
            raise ValueError("--output cannot be combined with --apply")
        target = Path(database)
    else:
        target = Path(output or (str(database) + ".aggregate-rehearsal.sqlite"))
        with private_output(target):
            pass
        with readonly(database) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    with closing(sqlite3.connect(target)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        if fingerprint(db) != manifest["live_fingerprint"]:
            raise ValueError("Database changed since export; take a fresh snapshot and replay again")
        for table in TABLES:
            db.execute(f"DELETE FROM {identifier(table)}")
            matching = [row for row in rows if row["table"] == table]
            db.executemany(f"INSERT INTO {identifier(table)} (monitor_id,timestamp,up,down,ping,ping_min,ping_max,extras) VALUES (?,?,?,?,?,?,?,?)", [tuple(row[field] for field in ("monitor_id", "timestamp", "up", "down", "ping", "ping_min", "ping_max", "extras")) for row in matching])
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("SQLite foreign key check failed")
        if fingerprint(db) != manifest["live_fingerprint"]:
            raise ValueError("Repair touched a non-aggregate table; transaction rolled back")
        db.commit()
    return {"database": str(target), "applied_to_requested_database": apply_live, "aggregate_rows": len(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    prepare = commands.add_parser("export")
    for option in ("backup", "live", "output", "manifest", "now"):
        prepare.add_argument("--" + option, required=True)
    repair = commands.add_parser("apply")
    for option in ("database", "aggregates", "manifest"):
        repair.add_argument("--" + option, required=True)
    repair.add_argument("--output")
    repair.add_argument("--apply", action="store_true", help="Explicitly replace aggregate tables in --database; otherwise rehearse on a copy")
    args = parser.parse_args()
    if args.command == "version":
        print(VERSION)
    elif args.command == "export":
        print(json.dumps(export(args.backup, args.live, args.output, args.manifest, args.now)))
    else:
        print(json.dumps(apply(args.database, args.aggregates, args.manifest, args.output, args.apply)))


if __name__ == "__main__":
    main()
