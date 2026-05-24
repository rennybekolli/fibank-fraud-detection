"""
telemetry_tracker.py  --  Fibank Fraud Detection Audit Logger
=============================================================

FraudLogger is a thread-safe, non-blocking telemetry module that builds
an append-only audit trail for every fraud scoring decision.

Architecture
------------
All write operations are dispatched to a single daemon thread via an
in-process queue (maxsize=10,000).  The Flask request handler calls
log_score_event() or update_resolution() and returns immediately --
the actual SQLite write happens in the background thread.

Impact on XGBoost inference latency: ~0 ms (queue.put_nowait is O(1)).

Closed-loop ML feedback
-----------------------
Every record stores the exact 27-feature vector evaluated at inference
time together with the downstream outcome (final_resolution).  The
get_ml_training_batch() method exports these as labelled samples ready
for XGBoost retraining:

    label = 0  (legitimate)  if resolution contains PASSED or AUTO_APPROVED
    label = 1  (fraud)       if resolution contains FAILED, BLOCKED, LOCKED

This closes the feedback loop: real transaction outcomes -- not just
synthetic data -- continuously improve the model.

Action constants   (set at /api/score time, immutable)
------------------
    Action.LOW_PASS         score < 3.0  -- auto-approved
    Action.MEDIUM_STEP_UP   3.0 <= score < 7.0  -- biometric challenge shown
    Action.HIGH_LOCKDOWN    score >= 7.0  -- FIDO + biometric overlay

Resolution constants   (set at /api/transfer time, nullable until then)
-----------------------
    Resolution.LOW_AUTO_APPROVED
    Resolution.PASSED_FACEID
    Resolution.PASSED_TOUCHID
    Resolution.PASSED_PASSKEY
    Resolution.PASSED_PUSH_NOTIFICATION
    Resolution.PASSED_ID_CARD_VERIFICATION
    Resolution.PASSED_FIDO_AND_BIOMETRIC
    Resolution.FAILED_FIDO_ACCOUNT_LOCKED
    Resolution.BLOCKED_HIGH_RISK
    Resolution.MEDIUM_ABANDONED
    Resolution.ANALYST_CLEARED
    Resolution.ANALYST_CONFIRMED_FRAUD
"""

import json
import logging
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("fibank.telemetry")


# ---------------------------------------------------------------------------
# Enumerations (plain string constants so SQLite stores them as readable text)
# ---------------------------------------------------------------------------

class Action:
    """Immutable: recorded at /api/score time from the risk tier."""
    LOW_PASS       = "LOW_PASS"
    MEDIUM_STEP_UP = "MEDIUM_STEP_UP"
    HIGH_LOCKDOWN  = "HIGH_LOCKDOWN"


class Resolution:
    """
    Mutable: updated at /api/transfer time once the user's auth
    challenge outcome is known.  NULL until that point.
    """
    # Successful paths
    LOW_AUTO_APPROVED     = "LOW_AUTO_APPROVED"
    PASSED_FACEID         = "PASSED_FACEID"
    PASSED_TOUCHID        = "PASSED_TOUCHID"
    PASSED_PASSKEY        = "PASSED_PASSKEY"
    PASSED_PUSH           = "PASSED_PUSH_NOTIFICATION"
    PASSED_ID_CARD        = "PASSED_ID_CARD_VERIFICATION"
    PASSED_FIDO_BIOMETRIC = "PASSED_FIDO_AND_BIOMETRIC"
    # Failed / blocked paths
    FAILED_FIDO_LOCKED    = "FAILED_FIDO_ACCOUNT_LOCKED"
    BLOCKED_HIGH_RISK     = "BLOCKED_HIGH_RISK"
    MEDIUM_ABANDONED      = "MEDIUM_ABANDONED"
    # Analyst-driven overrides (for future SOC integration)
    ANALYST_CLEARED       = "MANUALLY_CLEARED_BY_ANALYST"
    ANALYST_FRAUD         = "ANALYST_CONFIRMED_FRAUD"

    # Map from JS method strings -> resolution constants
    METHOD_MAP: dict = {
        "faceid":   PASSED_FACEID,
        "touchid":  PASSED_TOUCHID,
        "passkey":  PASSED_PASSKEY,
        "push":     PASSED_PUSH,
        "id_card":  PASSED_ID_CARD,
    }

    @classmethod
    def from_transfer(
        cls,
        status: str,
        risk_level: str,
        method: Optional[str] = None,
    ) -> str:
        """
        Derives the correct resolution string from the /api/transfer payload.

        status      -- 'completed' or 'blocked'
        risk_level  -- 'low', 'medium', 'high'
        method      -- optional auth method string from JS ('faceid' etc.)
        """
        if status == "blocked":
            return (cls.FAILED_FIDO_LOCKED
                    if risk_level == "high"
                    else cls.BLOCKED_HIGH_RISK)
        if risk_level == "low":
            return cls.LOW_AUTO_APPROVED
        if risk_level == "medium":
            return cls.METHOD_MAP.get(method or "", cls.PASSED_FACEID)
        # high + completed
        return cls.PASSED_FIDO_BIOMETRIC


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry_audit_log (
    id                       INTEGER  PRIMARY KEY AUTOINCREMENT,
    transaction_id           TEXT     NOT NULL UNIQUE,
    user_id                  INTEGER  NOT NULL,
    session_id               TEXT     NOT NULL,
    timestamp_utc            TEXT     NOT NULL,
    feature_snapshot         TEXT     NOT NULL,
    predicted_risk_score     REAL     NOT NULL,
    model_version            TEXT     NOT NULL DEFAULT '1.0.0',
    action_taken             TEXT     NOT NULL
                             CHECK(action_taken IN ('LOW_PASS','MEDIUM_STEP_UP','HIGH_LOCKDOWN')),
    amount                   REAL     NOT NULL DEFAULT 0.0,
    final_resolution         TEXT,
    resolution_timestamp_utc TEXT,
    analyst_notes            TEXT,
    created_at               TEXT     NOT NULL
                             DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_user_id
    ON telemetry_audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_action
    ON telemetry_audit_log (action_taken);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON telemetry_audit_log (timestamp_utc DESC);
"""

_INSERT = """
INSERT INTO telemetry_audit_log
    (transaction_id, user_id, session_id, timestamp_utc,
     feature_snapshot, predicted_risk_score, model_version,
     action_taken, amount)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_UPDATE_RESOLUTION = """
UPDATE telemetry_audit_log
SET    final_resolution         = ?,
       resolution_timestamp_utc = ?
WHERE  transaction_id           = ?
"""

_UPDATE_ANALYST = """
UPDATE telemetry_audit_log
SET    final_resolution         = ?,
       analyst_notes            = ?,
       resolution_timestamp_utc = ?
WHERE  transaction_id           = ?
"""


# ---------------------------------------------------------------------------
# FraudLogger
# ---------------------------------------------------------------------------

class FraudLogger:
    """
    Thread-safe, non-blocking audit logger.

    All public methods return immediately.  A single daemon thread
    drains the queue and executes the SQLite writes in the background.

    Parameters
    ----------
    db_path       Path to the SQLite database file.
    model_version Short identifier for the XGBoost model in use.
                  Stored in every audit row so post-hoc analysis can
                  attribute score differences to model version changes.
    queue_maxsize  Maximum pending operations before drops occur.
                  At 10,000 entries this is ~50 MB RAM worst-case.
    """

    def __init__(
        self,
        db_path: str,
        model_version: str = "1.0.0",
        queue_maxsize: int = 10_000,
    ) -> None:
        self._db_path      = str(db_path)
        self._model_ver    = model_version
        self._q: queue.Queue = queue.Queue(maxsize=queue_maxsize)
        self._dropped      = 0          # counter for monitoring
        self._written      = 0          # counter for monitoring

        # Ensure table exists on startup (idempotent)
        self._ensure_schema()

        self._worker = threading.Thread(
            target=self._drain,
            daemon=True,
            name="fraud-telemetry",
        )
        self._worker.start()
        log.info("FraudLogger started  db=%s  model=%s", db_path, model_version)

    # ------------------------------------------------------------------
    # Public API  (all fire-and-forget)
    # ------------------------------------------------------------------

    def log_score_event(
        self,
        *,
        transaction_id: str,
        user_id: int,
        session_id: str,
        features: dict,
        predicted_score: float,
        action: str,
        amount: float,
    ) -> None:
        """
        Queue a new audit row immediately after XGBoost inference.

        The feature_snapshot is JSON-serialised here (on the request thread)
        so the background thread only needs to do a simple string write.
        Serialisation of 27 floats takes < 0.1 ms.
        """
        self._enqueue({
            "op":               "insert",
            "transaction_id":   transaction_id,
            "user_id":          user_id,
            "session_id":       session_id,
            "timestamp_utc":    datetime.now(timezone.utc).isoformat(),
            "feature_snapshot": json.dumps(features, default=str),
            "score":            round(float(predicted_score), 4),
            "model_version":    self._model_ver,
            "action":           action,
            "amount":           float(amount),
        })

    def update_resolution(
        self,
        *,
        transaction_id: str,
        resolution: str,
    ) -> None:
        """
        Queue an UPDATE to set final_resolution on an existing row.

        Called once the user's auth challenge outcome is known:
          - /api/transfer status='completed' or status='blocked'
        """
        self._enqueue({
            "op":             "update",
            "transaction_id": transaction_id,
            "resolution":     resolution,
            "ts":             datetime.now(timezone.utc).isoformat(),
        })

    def analyst_override(
        self,
        *,
        transaction_id: str,
        resolution: str,
        notes: str,
    ) -> None:
        """
        Queue an analyst-driven override (SOC/compliance team).

        Writes both final_resolution and analyst_notes in a single UPDATE.
        Intended for the future analyst dashboard endpoint.
        """
        self._enqueue({
            "op":             "analyst",
            "transaction_id": transaction_id,
            "resolution":     resolution,
            "notes":          notes,
            "ts":             datetime.now(timezone.utc).isoformat(),
        })

    def flush(self, timeout: float = 5.0) -> None:
        """
        Block until the queue is empty (all writes committed).
        Use in tests or shutdown hooks -- never in request handlers.
        """
        self._q.join()

    @property
    def stats(self) -> dict:
        return {
            "queue_depth": self._q.qsize(),
            "written":     self._written,
            "dropped":     self._dropped,
        }

    # ------------------------------------------------------------------
    # Synchronous query helpers  (read-only, never on the hot path)
    # ------------------------------------------------------------------

    def get_recent(self, limit: int = 50) -> list:
        """Returns the most recent audit rows, newest first."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM telemetry_audit_log "
                "ORDER BY timestamp_utc DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_pending(self) -> list:
        """Rows where final_resolution IS NULL (auth challenge in-flight)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT transaction_id, user_id, timestamp_utc, "
                "       predicted_risk_score, action_taken, amount "
                "FROM   telemetry_audit_log "
                "WHERE  final_resolution IS NULL "
                "ORDER  BY timestamp_utc DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_ml_training_batch(self, resolved_only: bool = True) -> list:
        """
        Export closed-loop records for XGBoost retraining.

        Each record contains:
            transaction_id      -- for deduplication
            features            -- dict of 27 features (parsed from JSON)
            predicted_score     -- what the current model scored
            ground_truth_label  -- 0=legit, 1=fraud (inferred from resolution)
            resolution          -- raw resolution string
            timestamp_utc       -- for temporal train/test splitting

        Label derivation:
            PASSED* or LOW_AUTO_APPROVED  ->  label = 0 (true negative)
            FAILED* or BLOCKED* or LOCKED* or FRAUD* -> label = 1 (true positive)
            NULL or other  ->  excluded (outcome unknown)

        This is the core ML feedback loop: every real transaction with a
        known outcome becomes a training sample for the next model version.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        where = "WHERE final_resolution IS NOT NULL" if resolved_only else ""
        try:
            rows = conn.execute(
                f"SELECT * FROM telemetry_audit_log {where} "
                "ORDER BY timestamp_utc ASC"
            ).fetchall()
        finally:
            conn.close()

        batch = []
        for row in rows:
            r   = dict(row)
            res = (r.get("final_resolution") or "").upper()

            if "PASSED" in res or "AUTO_APPROVED" in res:
                label = 0
            elif any(k in res for k in ("FAILED", "BLOCKED", "LOCKED", "FRAUD")):
                label = 1
            else:
                continue  # ambiguous resolution -- skip

            try:
                features = json.loads(r["feature_snapshot"])
            except (json.JSONDecodeError, TypeError):
                log.warning("Could not parse feature_snapshot for %s",
                            r.get("transaction_id"))
                continue

            batch.append({
                "transaction_id":     r["transaction_id"],
                "features":           features,
                "predicted_score":    r["predicted_risk_score"],
                "ground_truth_label": label,
                "resolution":         r["final_resolution"],
                "timestamp_utc":      r["timestamp_utc"],
                "model_version":      r["model_version"],
            })

        return batch

    def summary_stats(self) -> dict:
        """
        Returns aggregate statistics for the audit log.
        Useful for the internal dashboard and compliance reports.
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM telemetry_audit_log"
            ).fetchone()[0]
            by_action = {
                r["action_taken"]: r["cnt"]
                for r in conn.execute(
                    "SELECT action_taken, COUNT(*) AS cnt "
                    "FROM telemetry_audit_log GROUP BY action_taken"
                )
            }
            by_resolution = {
                r["final_resolution"]: r["cnt"]
                for r in conn.execute(
                    "SELECT final_resolution, COUNT(*) AS cnt "
                    "FROM telemetry_audit_log "
                    "GROUP BY final_resolution ORDER BY cnt DESC"
                )
            }
            pending = conn.execute(
                "SELECT COUNT(*) FROM telemetry_audit_log "
                "WHERE final_resolution IS NULL"
            ).fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(predicted_risk_score) FROM telemetry_audit_log"
            ).fetchone()[0]
        finally:
            conn.close()

        return {
            "total_events":     total,
            "pending_outcomes": pending,
            "avg_risk_score":   round(avg_score or 0.0, 3),
            "by_action":        by_action,
            "by_resolution":    by_resolution,
            "runtime_stats":    self.stats,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _enqueue(self, item: dict) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            self._dropped += 1
            log.error(
                "FraudLogger queue full -- audit record DROPPED  "
                "transaction_id=%s  dropped_total=%d",
                item.get("transaction_id", "?"),
                self._dropped,
            )

    def _drain(self) -> None:
        """Background daemon: pull items from queue and write to SQLite."""
        while True:
            item = self._q.get()
            try:
                self._write(item)
                self._written += 1
            except Exception:
                log.exception(
                    "FraudLogger write error  transaction_id=%s",
                    item.get("transaction_id", "?"),
                )
            finally:
                self._q.task_done()

    def _write(self, item: dict) -> None:
        """Executes the SQLite write. Runs in the background thread only."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        try:
            op = item["op"]
            if op == "insert":
                conn.execute(_INSERT, (
                    item["transaction_id"],
                    item["user_id"],
                    item["session_id"],
                    item["timestamp_utc"],
                    item["feature_snapshot"],
                    item["score"],
                    item["model_version"],
                    item["action"],
                    item["amount"],
                ))
            elif op == "update":
                conn.execute(_UPDATE_RESOLUTION, (
                    item["resolution"],
                    item["ts"],
                    item["transaction_id"],
                ))
            elif op == "analyst":
                conn.execute(_UPDATE_ANALYST, (
                    item["resolution"],
                    item["notes"],
                    item["ts"],
                    item["transaction_id"],
                ))
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Creates the audit table if it doesn't already exist."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.executescript(_CREATE_TABLE)
            conn.commit()
            conn.close()
        except Exception:
            log.exception("FraudLogger: failed to ensure schema")
