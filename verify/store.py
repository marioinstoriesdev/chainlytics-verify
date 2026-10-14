"""SQLite persistence: checks, verdicts, receipts (with raw responses) and daily roots."""
import gzip
import json
import sqlite3
import time

from .receipts import canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, created_ms INTEGER NOT NULL,
  slot_before INTEGER, slot_after INTEGER, origin TEXT, result_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS checks_mint ON checks(mint, created_ms);
CREATE TABLE IF NOT EXISTS verdicts (check_id INTEGER NOT NULL, mint TEXT NOT NULL, created_ms INTEGER NOT NULL,
  source TEXT NOT NULL, metric TEXT NOT NULL, status TEXT NOT NULL, definition TEXT, origin TEXT);
CREATE INDEX IF NOT EXISTS verdicts_time ON verdicts(created_ms);
CREATE TABLE IF NOT EXISTS receipts (check_id INTEGER NOT NULL, idx INTEGER NOT NULL, kind TEXT, target TEXT,
  params_json TEXT, slot INTEGER, ts_ms INTEGER, sha256 TEXT NOT NULL, raw_gz BLOB, PRIMARY KEY (check_id, idx));
CREATE INDEX IF NOT EXISTS receipts_time ON receipts(ts_ms);
CREATE TABLE IF NOT EXISTS daily_roots (day TEXT PRIMARY KEY, root TEXT NOT NULL, n INTEGER NOT NULL,
  computed_ms INTEGER NOT NULL, memo_sig TEXT);
"""


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)

    def save(self, result, recorder, origin):
        now = result["created_ms"]
        cur = self.db.execute(
            "INSERT INTO checks (mint, created_ms, slot_before, slot_after, origin, result_json) VALUES (?,?,?,?,?,?)",
            (result["mint"], now, result["slots"]["before"], result["slots"]["after"], origin, json.dumps(result, default=str)))
        cid = cur.lastrowid
        self.db.executemany(
            "INSERT INTO verdicts VALUES (?,?,?,?,?,?,?,?)",
            [(cid, result["mint"], now, v["source"], v["metric"], v["status"], v.get("definition"), origin)
             for v in result["verdicts"]])
        self.db.executemany(
            "INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?)",
            [(cid, i, r["kind"], r["target"], json.dumps(r["params"], default=str), r["slot"], r["ts_ms"], r["sha256"],
              gzip.compress(canonical(raw).encode("utf-8"))) for i, (r, raw) in enumerate(recorder.items)])
        self.db.commit()
        return cid

    def latest(self, mint, max_age_ms):
        row = self.db.execute("SELECT id, result_json FROM checks WHERE mint = ? AND created_ms >= ? ORDER BY id DESC LIMIT 1",
                              (mint, int(time.time() * 1000) - max_age_ms)).fetchone()
        return (row[0], json.loads(row[1])) if row else (None, None)

    def check(self, cid):
        row = self.db.execute("SELECT result_json FROM checks WHERE id = ?", (cid,)).fetchone()
        return json.loads(row[0]) if row else None

    def receipts(self, cid):
        return [{"idx": r[0], "kind": r[1], "target": r[2], "params": json.loads(r[3]), "slot": r[4], "ts_ms": r[5], "sha256": r[6]}
                for r in self.db.execute("SELECT idx, kind, target, params_json, slot, ts_ms, sha256 FROM receipts "
                                         "WHERE check_id = ? ORDER BY idx", (cid,))]

    def raw(self, cid, idx):
        row = self.db.execute("SELECT raw_gz FROM receipts WHERE check_id = ? AND idx = ?", (cid, idx)).fetchone()
        return json.loads(gzip.decompress(row[0]).decode("utf-8")) if row else None

    def verdict_rows(self, since_ms, origin=None):
        q = "SELECT source, metric, status, definition FROM verdicts WHERE created_ms >= ?"
        args = [since_ms]
        if origin:
            q += " AND origin = ?"
            args.append(origin)
        return self.db.execute(q, args).fetchall()

    def results(self, since_ms, origin=None):
        q = "SELECT result_json FROM checks WHERE created_ms >= ?"
        args = [since_ms]
        if origin:
            q += " AND origin = ?"
            args.append(origin)
        return [json.loads(r[0]) for r in self.db.execute(q, args)]

    def receipt_hashes(self, start_ms, end_ms):
        return [r[0] for r in self.db.execute("SELECT sha256 FROM receipts WHERE ts_ms >= ? AND ts_ms < ?", (start_ms, end_ms))]

    def save_root(self, day, root, n):
        self.db.execute("INSERT INTO daily_roots (day, root, n, computed_ms) VALUES (?,?,?,?) "
                        "ON CONFLICT(day) DO UPDATE SET root = excluded.root, n = excluded.n, computed_ms = excluded.computed_ms",
                        (day, root, n, int(time.time() * 1000)))
        self.db.commit()

    def record_memo(self, day, sig):
        self.db.execute("UPDATE daily_roots SET memo_sig = ? WHERE day = ?", (sig, day))
        self.db.commit()

    def root(self, day):
        row = self.db.execute("SELECT day, root, n, computed_ms, memo_sig FROM daily_roots WHERE day = ?", (day,)).fetchone()
        return dict(zip(("day", "root", "n", "computed_ms", "memo_sig"), row)) if row else None
