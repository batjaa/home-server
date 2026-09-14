"""SQLite recovery fixtures: deduplication, complete buckets, and atomicity."""

import importlib.util
from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).parents[1] / "roles/containers/monitoring/uptime-kuma/files/recover-aggregates.py"
spec = importlib.util.spec_from_file_location("kuma_recovery", MODULE)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@contextmanager
def connect(path):
    with closing(sqlite3.connect(path)) as db, db:
        yield db


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original = self.root / "original.db"
        self.live = self.root / "live.db"
        self.raw = self.root / "heartbeats.jsonl"
        self.manifest = self.root / "manifest.json"
        self.aggregates = self.root / "aggregates.jsonl"
        self.now = "2026-09-14T12:00:30Z"
        for path in (self.original, self.live):
            with connect(path) as db:
                db.executescript("""
                    CREATE TABLE monitor(id INTEGER PRIMARY KEY, name TEXT);
                    INSERT INTO monitor VALUES(1,'existing monitor');
                    CREATE TABLE setting(key TEXT PRIMARY KEY, value TEXT);
                    INSERT INTO setting VALUES('secret','must remain untouched');
                    CREATE TABLE heartbeat(id INTEGER PRIMARY KEY AUTOINCREMENT,
                        monitor_id INTEGER, status INTEGER, ping REAL, time TEXT);
                """)
                for table in recovery.TABLES:
                    db.execute(f"CREATE TABLE {table}(id INTEGER PRIMARY KEY AUTOINCREMENT, monitor_id INTEGER REFERENCES monitor(id), timestamp INTEGER, up INTEGER, down INTEGER, ping REAL, ping_min REAL, ping_max REAL, extras TEXT)")
                db.executemany("INSERT INTO heartbeat VALUES(?,?,?,?,?)", [
                    (1, 1, 1, 10, "2026-09-13 23:59:00.000"),
                    (2, 1, 1, 20, "2026-09-14 00:00:00.000"),
                    (3, 1, 2, 0, "2026-09-14 00:01:00.000"),
                ])
        with connect(self.live) as db:
            db.execute("DELETE FROM heartbeat WHERE id=1")
            db.execute("INSERT INTO heartbeat VALUES(4,1,3,NULL,'2026-09-14 00:02:00.000')")
            db.execute("INSERT INTO stat_daily(monitor_id,timestamp,up,down,ping,ping_min,ping_max) VALUES(1,0,999,0,99,99,99)")

    def prepare(self):
        return recovery.export(self.original, self.live, self.raw, self.manifest, self.now)

    def emit_valid_aggregates(self):
        manifest = json.loads(self.manifest.read_text())
        rows = []
        for key, stats in manifest["expected"].items():
            table, monitor, timestamp = key.split("/")
            row = {"table": table, "monitor_id": int(monitor), "timestamp": int(timestamp)}
            row.update({field: stats[field] for field in ("up", "down", "ping", "ping_min", "ping_max")})
            row["extras"] = json.dumps({"maintenance": stats["maintenance"]}) if stats["maintenance"] else None
            rows.append(row)
        self.aggregates.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return rows

    def test_union_recovers_pruned_history_without_duplicate_overlap(self):
        report = self.prepare()
        self.assertEqual(report["samples"], 4)
        self.assertEqual(report["live_delta"], 1)
        raw = [json.loads(line) for line in self.raw.read_text().splitlines()]
        self.assertEqual([row[1] for row in raw], [1, 1, 2, 3])
        expected = json.loads(self.manifest.read_text())["expected"]
        days = {key: row for key, row in expected.items() if key.startswith("stat_daily/")}
        self.assertEqual(len(days), 2)
        self.assertEqual(sum(row["up"] for row in days.values()), 2)
        self.assertEqual(sum(row["down"] for row in days.values()), 1)
        self.assertEqual(sum(row["maintenance"] for row in days.values()), 1)
        self.assertNotIn("must remain untouched", self.manifest.read_text())

    def test_rejects_conflicting_overlap_and_regressed_sequence(self):
        with connect(self.live) as db:
            db.execute("UPDATE heartbeat SET ping=200 WHERE id=2")
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.prepare()
        with connect(self.live) as db:
            db.execute("UPDATE heartbeat SET ping=20 WHERE id=2")
            db.execute("UPDATE sqlite_sequence SET seq=1 WHERE name='heartbeat'")
        with self.assertRaisesRegex(ValueError, "regressed"):
            self.prepare()

    def test_refuses_repair_after_new_raw_history_has_expired(self):
        with connect(self.live) as db:
            db.execute("DELETE FROM heartbeat WHERE id=4")
        with self.assertRaisesRegex(ValueError, "Post-backup heartbeat history has gaps"):
            self.prepare()
        self.assertFalse(self.raw.exists())
        self.assertFalse(self.manifest.exists())

    def test_default_rehearsal_preserves_live_and_nonaggregate_tables(self):
        self.prepare()
        self.emit_valid_aggregates()
        old_bytes = self.live.read_bytes()
        result = recovery.apply(self.live, self.aggregates, self.manifest)
        self.assertFalse(result["applied_to_requested_database"])
        self.assertEqual(self.live.read_bytes(), old_bytes)
        with connect(result["database"]) as repaired, connect(self.live) as live:
            self.assertEqual(recovery.fingerprint(repaired), recovery.fingerprint(live))
            self.assertEqual(repaired.execute("SELECT sum(up) FROM stat_daily").fetchone()[0], 2)
            self.assertEqual(live.execute("SELECT sum(up) FROM stat_daily").fetchone()[0], 999)

    def test_refuses_stale_snapshot_and_bad_aggregates_without_writes(self):
        self.prepare()
        rows = self.emit_valid_aggregates()
        with connect(self.live) as db:
            db.execute("UPDATE setting SET value='new setting' WHERE key='secret'")
        old_bytes = self.live.read_bytes()
        with self.assertRaisesRegex(ValueError, "changed since export"):
            recovery.apply(self.live, self.aggregates, self.manifest, apply_live=True)
        self.assertEqual(self.live.read_bytes(), old_bytes)
        for damaged in ([*rows, rows[0]], rows[:-1], [{**rows[0], "up": 100}, *rows[1:]], [{**rows[0], "ping": 999}, *rows[1:]]):
            self.aggregates.write_text("".join(json.dumps(row) + "\n" for row in damaged))
            with self.assertRaises(ValueError):
                recovery.apply(self.live, self.aggregates, self.manifest, apply_live=True)
            self.assertEqual(self.live.read_bytes(), old_bytes)

    def test_explicit_apply_changes_only_aggregate_rows(self):
        self.prepare()
        self.emit_valid_aggregates()
        with connect(self.live) as db:
            before = recovery.fingerprint(db)
        report = recovery.apply(self.live, self.aggregates, self.manifest, apply_live=True)
        self.assertTrue(report["applied_to_requested_database"])
        with connect(self.live) as db:
            self.assertEqual(recovery.fingerprint(db), before)
            self.assertEqual(db.execute("SELECT sum(up) FROM stat_daily").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM heartbeat").fetchone()[0], 3)

    def test_transaction_rolls_back_if_trigger_touches_settings(self):
        with connect(self.live) as db:
            db.execute("CREATE TRIGGER corrupt AFTER INSERT ON stat_daily BEGIN UPDATE setting SET value='unexpected' WHERE key='secret'; END")
        self.prepare()
        self.emit_valid_aggregates()
        with self.assertRaisesRegex(ValueError, "non-aggregate"):
            recovery.apply(self.live, self.aggregates, self.manifest, apply_live=True)
        with connect(self.live) as db:
            self.assertEqual(db.execute("SELECT value FROM setting").fetchone()[0], "must remain untouched")
            self.assertEqual(db.execute("SELECT sum(up) FROM stat_daily").fetchone()[0], 999)

    def test_retention_keeps_complete_boundary_buckets(self):
        with connect(self.original) as db:
            db.execute("INSERT INTO heartbeat VALUES(5,1,1,30,'2026-09-13 12:00:01.000')")
        with connect(self.live) as db:
            db.execute("INSERT INTO heartbeat VALUES(5,1,1,30,'2026-09-13 12:00:01.000')")
        self.prepare()
        expected = json.loads(self.manifest.read_text())["expected"]
        epoch = int(recovery.utc_timestamp("2026-09-13T12:00:00Z"))
        self.assertIn(f"stat_minutely/1/{epoch}", expected)


if __name__ == "__main__":
    unittest.main()
