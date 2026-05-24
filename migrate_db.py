#!/usr/bin/env python3
"""
migrate_db.py  --  Fibank DB Migration v2
==========================================
Adds the telemetry_audit_log table, three query indexes, and two
analytical views to an existing db.sqlite3.

Safe to run multiple times -- all statements use IF NOT EXISTS.

Usage:
    python migrate_db.py              # targets ./db.sqlite3
    python migrate_db.py --db path/to/db.sqlite3
    python migrate_db.py --verify     # schema report only, no writes
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_AUDIT_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry_audit_log (
    -- Surrogate key (internal ordering only)
    id                       INTEGER  PRIMARY KEY AUTOINCREMENT,

    -- Immutable identity fields (set at INSERT, never changed)
    transaction_id           TEXT     NOT NULL UNIQUE,   -- UUID4 from /api/score
    user_id                  INTEGER  NOT NULL,
    session_id               TEXT     NOT NULL,           -- Flask session UUID
    timestamp_utc            TEXT     NOT NULL,           -- ISO-8601 UTC
    feature_snapshot         TEXT     NOT NULL,           -- JSON of 27-feature vector
    predicted_risk_score     REAL     NOT NULL,
    model_version            TEXT     NOT NULL DEFAULT '1.0.0',
    action_taken             TEXT     NOT NULL            -- LOW_PASS | MEDIUM_STEP_UP | HIGH_LOCKDOWN
                             CHECK(action_taken IN ('LOW_PASS','MEDIUM_STEP_UP','HIGH_LOCKDOWN')),
    amount                   REAL     NOT NULL DEFAULT 0.0,

    -- Mutable outcome fields (updated after auth challenge completes)
    final_resolution         TEXT,                        -- see Resolution constants
    resolution_timestamp_utc TEXT,
    analyst_notes            TEXT,

    -- Auto-timestamp (UTC, set by SQLite on insert)
    created_at               TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""

CREATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audit_user_id
    ON telemetry_audit_log (user_id);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON telemetry_audit_log (action_taken);

CREATE INDEX IF NOT EXISTS idx_audit_resolution
    ON telemetry_audit_log (final_resolution);

CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON telemetry_audit_log (timestamp_utc DESC);
"""

CREATE_VIEWS = """
-- Rows where the auth challenge outcome is still unknown
CREATE VIEW IF NOT EXISTS vw_pending_outcomes AS
SELECT  transaction_id,
        user_id,
        timestamp_utc,
        predicted_risk_score,
        action_taken,
        amount
FROM    telemetry_audit_log
WHERE   final_resolution IS NULL
ORDER   BY timestamp_utc DESC;

-- Closed-loop rows where fraud was confirmed (for XGBoost retraining)
CREATE VIEW IF NOT EXISTS vw_confirmed_fraud AS
SELECT  *
FROM    telemetry_audit_log
WHERE   final_resolution LIKE '%FAILED%'
   OR   final_resolution LIKE '%BLOCKED%'
   OR   final_resolution LIKE '%FRAUD%'
   OR   final_resolution LIKE '%LOCKED%';

-- Clean legitimate transactions (negative training labels)
CREATE VIEW IF NOT EXISTS vw_confirmed_legit AS
SELECT  *
FROM    telemetry_audit_log
WHERE   final_resolution LIKE '%PASSED%'
   OR   final_resolution = 'LOW_AUTO_APPROVED';
"""


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------

def run_migration(db_path: Path, verify_only: bool = False) -> None:
    if not db_path.exists():
        print(f"ERROR: {db_path} not found.")
        print("Run the Flask app once first so init_db() creates the base schema.")
        sys.exit(1)

    if verify_only:
        _print_schema_report(db_path)
        return

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(CREATE_AUDIT_TABLE)
        conn.executescript(CREATE_INDEXES)
        conn.executescript(CREATE_VIEWS)
        conn.commit()
        print(f"[OK] Migration applied to {db_path.resolve()}")
    finally:
        conn.close()

    _print_schema_report(db_path)


def _print_schema_report(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]
    views = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    )]
    indexes = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
    )]

    audit_exists = "telemetry_audit_log" in tables
    if audit_exists:
        cols = conn.execute(
            'SELECT name, type, "notnull", dflt_value '
            "FROM pragma_table_info('telemetry_audit_log')"
        ).fetchall()
        row_count = conn.execute(
            "SELECT COUNT(*) FROM telemetry_audit_log"
        ).fetchone()[0]
    conn.close()

    sep = "-" * 60
    print(sep)
    print("  Schema Report")
    print(sep)
    print(f"  Tables  : {tables}")
    print(f"  Views   : {views}")
    print(f"  Indexes : {[i for i in indexes if 'audit' in i]}")

    if audit_exists:
        print(f"\n  telemetry_audit_log ({row_count} rows)")
        for c in cols:
            nn = "NOT NULL" if c[2] else "nullable"
            df = f"  default={c['dflt_value']}" if c["dflt_value"] else ""
            print(f"    {c['name']:<30} {c['type']:<8} {nn}{df}")
    else:
        print("\n  WARNING: telemetry_audit_log not found -- migration may have failed")
    print(sep)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Fibank DB migration v2")
    p.add_argument("--db",     default="db.sqlite3",
                   help="Path to SQLite database (default: db.sqlite3)")
    p.add_argument("--verify", action="store_true",
                   help="Print schema report only, no DDL changes")
    args = p.parse_args()

    run_migration(Path(args.db), verify_only=args.verify)
