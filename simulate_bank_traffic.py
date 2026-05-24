#!/usr/bin/env python3
"""
simulate_bank_traffic.py  --  Fibank Fraud Detection  |  Production Load Test
==============================================================================

Fires 500 concurrent telemetry transactions at the live Flask instance using
ThreadPoolExecutor(20 workers), then queries telemetry_audit_log directly and
prints an executive-grade terminal report.

Scenario distribution
---------------------
  95 %  SAFE      -- normal behavioural biometrics, historical payee, small amounts
   4 %  MEDIUM    -- neobank routing, tz mismatch, elevated mouse/typing scores
   1 %  CRITICAL  -- call overlay, screen-share, VM, remote access, large transfer

Each worker flow per transaction
---------------------------------
  1. POST /api/presenter/set-signals   -- loads scenario signals into Flask session
  2. POST /api/score                   -- builds 27-feature vector + logs audit row
  3. POST /api/transfer                -- resolves outcome, closes audit loop

Usage
-----
  Ensure Flask is running:  python app.py
  Then:                     python simulate_bank_traffic.py
"""

import json
import random
import sqlite3
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_URL     = "http://127.0.0.1:5000"
NUM_REQUESTS = 500
NUM_WORKERS  = 20
DB_PATH      = "db.sqlite3"
RANDOM_SEED  = 42
TIMEOUT_SEC  = 30


# ── Scenario builders (called in main thread -- RNG is not thread-shared) ──────

def _make_safe_signals(rng: random.Random) -> dict:
    return {
        "is_historical_payee":        1,
        "is_known_location":          1,
        "used_fido_passkey":          1,
        "ip_asn_type":                "residential",
        "timezone_mismatch":          0,
        "mouse_linearity_score":      round(rng.uniform(0.02, 0.15), 3),
        "typing_cadence_score":       round(rng.uniform(0.02, 0.15), 3),
        "is_neobank_routing":         0,
        "is_vm_or_emulator":          0,
        "webdriver_detected":         0,
        "bot_agility_index":          0,
        "is_in_active_call":          0,
        "is_screensharing_active":    0,
        "remote_access_app_detected": 0,
        "coached_fraud_index":        0,
        "session_from_link":          0,
        "mule_potential":             0,
        "session_tension":            0,
    }


def _make_medium_signals(rng: random.Random) -> dict:
    return {
        "is_historical_payee":        rng.choice([0, 0, 1]),
        "is_known_location":          1,
        "used_fido_passkey":          0,
        "ip_asn_type":                "business",
        "timezone_mismatch":          1,
        "mouse_linearity_score":      round(rng.uniform(0.55, 0.75), 3),
        "typing_cadence_score":       round(rng.uniform(0.40, 0.65), 3),
        "is_neobank_routing":         1,
        "is_vm_or_emulator":          0,
        "webdriver_detected":         0,
        "bot_agility_index":          0,
        "is_in_active_call":          0,
        "is_screensharing_active":    0,
        "remote_access_app_detected": 0,
        "coached_fraud_index":        0,
        "session_from_link":          0,
        "mule_potential":             0,
        "session_tension":            0,
    }


def _make_critical_signals(rng: random.Random) -> dict:
    return {
        "is_historical_payee":        0,
        "is_known_location":          0,
        "used_fido_passkey":          0,
        "ip_asn_type":                "hosting",
        "timezone_mismatch":          1,
        "mouse_linearity_score":      round(rng.uniform(0.82, 0.99), 3),
        "typing_cadence_score":       round(rng.uniform(0.82, 0.99), 3),
        "is_neobank_routing":         1,
        "is_vm_or_emulator":          1,
        "webdriver_detected":         1,
        "bot_agility_index":          1,
        "is_in_active_call":          1,
        "is_screensharing_active":    1,
        "remote_access_app_detected": 1,
        "coached_fraud_index":        1,
        "session_from_link":          1,
        "mule_potential":             0,
        "session_tension":            0,
    }


def _make_score_payload(scenario: str, idx: int, rng: random.Random) -> dict:
    recipient_names = [
        "Elton Hoxha", "Arben Malaj", "Rinas Logistics SH.P.K",
        "Ergys Koci", "Blerina Gjoka", "TechTrade SHPK",
        "Fatmir Shehu", "Anila Cela",
    ]
    iban_suffix = f"{rng.randint(10000, 99999):05d} {rng.randint(1000, 9999):04d}"

    base = {
        "recipient_name": rng.choice(recipient_names),
        "recipient_iban": f"AL47 2121 1009 0000 000{rng.randint(1, 9)} {iban_suffix}",
    }

    if scenario == "safe":
        base.update({
            "amount":                     round(rng.uniform(500,    50_000), 2),
            "form_completion_time_sec":   round(rng.uniform(12,     45),     1),
            "password_entry_ms":          round(rng.uniform(1500,   4000),   0),
            "pages_visited_pre_transfer": rng.randint(2, 8),
            "time_login_to_transfer_sec": round(rng.uniform(40,     180),    1),
        })
    elif scenario == "medium":
        base.update({
            "amount":                     round(rng.uniform(50_001,  88_000), 2),
            "form_completion_time_sec":   round(rng.uniform(5,       15),     1),
            "password_entry_ms":          round(rng.uniform(400,     900),    0),
            "pages_visited_pre_transfer": rng.randint(1, 3),
            "time_login_to_transfer_sec": round(rng.uniform(20,      55),     1),
        })
    else:  # critical
        base.update({
            "amount":                     round(rng.uniform(100_001, 300_000), 2),
            "form_completion_time_sec":   round(rng.uniform(2,       8),       1),
            "password_entry_ms":          round(rng.uniform(100,     300),     0),
            "pages_visited_pre_transfer": rng.randint(1, 2),
            "time_login_to_transfer_sec": round(rng.uniform(8,       25),      1),
        })

    return base


# ── Job bundle (immutable, pre-generated, passed to worker threads) ────────────

@dataclass
class TransactionJob:
    index:             int
    scenario:          str
    presenter_signals: dict
    score_payload:     dict


@dataclass
class TransactionResult:
    index:      int
    scenario:   str
    http_code:  int
    risk_level: Optional[str]    = None
    risk_score: Optional[float]  = None
    audit_id:   Optional[str]    = None
    action:     Optional[str]    = None
    latency_ms: float            = 0.0
    error:      Optional[str]    = None


# ── Worker (stateless -- each call gets its own requests.Session) ──────────────

def run_job(job: TransactionJob) -> TransactionResult:
    sess = requests.Session()

    try:
        # Step 1: Inject scenario signals into this session's Flask context
        sess.post(
            f"{BASE_URL}/api/presenter/set-signals",
            json=job.presenter_signals,
            timeout=TIMEOUT_SEC,
        )

        # Step 2: Request a fraud score and start the latency clock
        t0 = time.perf_counter()
        resp = sess.post(
            f"{BASE_URL}/api/score",
            json=job.score_payload,
            timeout=TIMEOUT_SEC,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            return TransactionResult(
                job.index, job.scenario, resp.status_code,
                latency_ms=latency_ms,
                error=f"HTTP {resp.status_code}",
            )

        result     = resp.json()
        risk_level = result.get("risk_level", "low")
        risk_score = result.get("risk_score", 0.0)
        audit_id   = result.get("audit_id")

        action = (
            "HIGH_LOCKDOWN"  if risk_level == "high"   else
            "MEDIUM_STEP_UP" if risk_level == "medium" else
            "LOW_PASS"
        )

        # Step 3: Close the audit loop with a transfer outcome
        transfer_status = "blocked" if risk_level == "high" else "completed"
        resolution_method = (
            "faceid"  if risk_level == "medium" else
            None
        )

        transfer_payload = {
            "audit_id":          audit_id,
            "amount":            job.score_payload["amount"],
            "recipient_name":    job.score_payload["recipient_name"],
            "recipient_iban":    job.score_payload["recipient_iban"],
            "risk_score":        risk_score,
            "risk_level":        risk_level,
            "triggered_signals": result.get("triggered_signals", []),
            "status":            transfer_status,
        }
        if resolution_method:
            transfer_payload["resolution_method"] = resolution_method

        sess.post(
            f"{BASE_URL}/api/transfer",
            json=transfer_payload,
            timeout=TIMEOUT_SEC,
        )

        return TransactionResult(
            job.index, job.scenario, 200,
            risk_level=risk_level,
            risk_score=risk_score,
            audit_id=audit_id,
            action=action,
            latency_ms=latency_ms,
        )

    except Exception as exc:
        return TransactionResult(
            job.index, job.scenario, 0,
            latency_ms=0.0,
            error=str(exc),
        )
    finally:
        sess.close()


# ── Pre-generate all jobs (deterministic, RNG stays in main thread) ────────────

def build_jobs(n: int = NUM_REQUESTS, seed: int = RANDOM_SEED) -> list:
    rng = random.Random(seed)
    slots: list[str] = []

    for _ in range(n):
        r = rng.random()
        if   r < 0.95: slots.append("safe")
        elif r < 0.99: slots.append("medium")
        else:          slots.append("critical")

    rng.shuffle(slots)

    jobs = []
    for idx, scenario in enumerate(slots):
        if   scenario == "safe":     signals = _make_safe_signals(rng)
        elif scenario == "medium":   signals = _make_medium_signals(rng)
        else:                        signals = _make_critical_signals(rng)
        payload = _make_score_payload(scenario, idx, rng)
        jobs.append(TransactionJob(idx, scenario, signals, payload))

    return jobs


# ── Terminal helpers ───────────────────────────────────────────────────────────

W = 72  # report width

def sep(char="="):   print(char * W)
def hdr(title):      sep(); print(f"  {title}"); sep()
def rule():          sep("-")
def pad(k, v, w=30): print(f"  {k:<{w}} : {v}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    hdr("FIBANK FRAUD DETECTION  --  PRODUCTION LOAD TEST")
    pad("Target",       BASE_URL)
    pad("Requests",     NUM_REQUESTS)
    pad("Workers",      NUM_WORKERS)
    pad("DB",           DB_PATH)
    rule()

    # Health-check
    try:
        requests.get(f"{BASE_URL}/", timeout=4)
    except Exception as exc:
        print(f"\n  ERROR: Flask not reachable at {BASE_URL}")
        print(f"  Start it with:  python app.py")
        print(f"  ({exc})")
        sys.exit(1)

    # Build job list
    jobs = build_jobs(NUM_REQUESTS, RANDOM_SEED)
    cnt  = {s: sum(1 for j in jobs if j.scenario == s) for s in ("safe","medium","critical")}

    print(f"\n  Scenario distribution (pre-generated, seed={RANDOM_SEED})")
    for scenario, label in (("safe","SAFE"), ("medium","MEDIUM"), ("critical","CRITICAL ATTACK")):
        n = cnt[scenario]
        print(f"    {label:<18} : {n:>4}  ({n/NUM_REQUESTS*100:5.1f}%)")

    print(f"\n  Firing {NUM_REQUESTS} requests across {NUM_WORKERS} parallel workers ...\n")

    results: list[TransactionResult] = []
    wall_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=NUM_WORKERS) as pool:
        futures = {pool.submit(run_job, j): j for j in jobs}
        done    = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 25 == 0 or done == NUM_REQUESTS:
                pct  = done / NUM_REQUESTS * 100
                fill = "#" * int(pct / 2)
                print(f"  [{fill:<50}] {done:>3}/{NUM_REQUESTS}  {pct:5.1f}%", end="\r", flush=True)

    wall_ms = (time.perf_counter() - wall_start) * 1000
    print(f"  [{'#'*50}] {NUM_REQUESTS}/{NUM_REQUESTS}  100.0%\n")

    # ── Wait for telemetry daemon to flush all queued writes ──────────────────
    print("  Waiting for telemetry queue to drain ...", end="", flush=True)
    flush_deadline = time.perf_counter() + 30
    while time.perf_counter() < flush_deadline:
        try:
            summary = requests.get(f"{BASE_URL}/api/audit/summary", timeout=5).json()
            q_depth = summary.get("runtime_stats", {}).get("queue_depth", 0)
            if q_depth == 0:
                break
        except Exception:
            pass
        time.sleep(0.3)
        print(".", end="", flush=True)
    time.sleep(0.8)   # one extra tick for the final SQLite commit
    print(" done\n", flush=True)

    # ── Classify results ───────────────────────────────────────────────────────
    ok      = [r for r in results if not r.error]
    errors  = [r for r in results if r.error]
    latencies = sorted(r.latency_ms for r in ok)

    action_counts = {"LOW_PASS": 0, "MEDIUM_STEP_UP": 0, "HIGH_LOCKDOWN": 0}
    for r in ok:
        if r.action in action_counts:
            action_counts[r.action] += 1

    total_ok = max(len(ok), 1)

    # ══════════════════════════════════════════════════════════════════════════
    hdr("EXECUTIVE REPORT")

    # -- Throughput -----------------------------------------------------------
    print("  THROUGHPUT")
    rule()
    pad("Requests fired",        NUM_REQUESTS)
    pad("Completed successfully", len(ok))
    pad("Errors / timeouts",     len(errors))
    pad("Wall-clock time",       f"{wall_ms/1000:.2f} s")
    pad("Throughput",            f"{len(ok) / (wall_ms/1000):.1f} req/s")

    if errors:
        print(f"\n  First 3 errors:")
        for r in errors[:3]:
            print(f"    [{r.scenario}] {r.error}")

    # -- Risk action distribution ---------------------------------------------
    print(f"\n  RISK ACTION DISTRIBUTION")
    rule()
    for action, count in action_counts.items():
        bar   = "#" * int(count / total_ok * 44)
        pct   = count / total_ok * 100
        print(f"  {action:<20} : {count:>4}  ({pct:5.1f}%)  [{bar}]")

    # -- Latency --------------------------------------------------------------
    if latencies:
        p95_idx = int(len(latencies) * 0.95)
        p99_idx = int(len(latencies) * 0.99)
        print(f"\n  API LATENCY  (round-trip /api/score,  n={len(latencies)})")
        rule()
        pad("Min",    f"{latencies[0]:.2f} ms")
        pad("Mean",   f"{statistics.mean(latencies):.2f} ms")
        pad("Median", f"{statistics.median(latencies):.2f} ms")
        pad("p95",    f"{latencies[p95_idx]:.2f} ms")
        pad("p99",    f"{latencies[p99_idx]:.2f} ms")
        pad("Max",    f"{latencies[-1]:.2f} ms")
        pad("Std dev",f"{statistics.stdev(latencies):.2f} ms")

    # -- Database verification ------------------------------------------------
    print(f"\n  DATABASE AUDIT VERIFICATION  ({DB_PATH})")
    rule()
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        total_db  = conn.execute("SELECT COUNT(*) FROM telemetry_audit_log").fetchone()[0]
        resolved  = conn.execute(
            "SELECT COUNT(*) FROM telemetry_audit_log WHERE final_resolution IS NOT NULL"
        ).fetchone()[0]
        pending   = total_db - resolved

        db_by_action = {
            r["action_taken"]: r["cnt"]
            for r in conn.execute(
                "SELECT action_taken, COUNT(*) AS cnt "
                "FROM telemetry_audit_log GROUP BY action_taken"
            )
        }
        db_by_res = {
            r["final_resolution"]: r["cnt"]
            for r in conn.execute(
                "SELECT final_resolution, COUNT(*) AS cnt "
                "FROM telemetry_audit_log "
                "WHERE final_resolution IS NOT NULL "
                "GROUP BY final_resolution ORDER BY cnt DESC"
            )
        }
        avg_score = conn.execute(
            "SELECT AVG(predicted_risk_score) FROM telemetry_audit_log"
        ).fetchone()[0] or 0.0

        pad("Total rows in audit log",    total_db)
        pad("Rows with resolved outcome", resolved)
        pad("Rows pending (NULL res.)",   pending)
        pad("Average predicted score",    f"{avg_score:.4f}")
        print()
        print("  Action distribution in DB:")
        for act, cnt_db in sorted(db_by_action.items()):
            print(f"    {act:<22} : {cnt_db}")
        print()
        print("  Resolution distribution in DB (top 8):")
        for res_key, cnt_db in list(db_by_res.items())[:8]:
            print(f"    {res_key:<35} : {cnt_db}")

        # -- Feature snapshot sample ------------------------------------------
        high_row = conn.execute(
            "SELECT transaction_id, predicted_risk_score, final_resolution, "
            "       timestamp_utc, feature_snapshot "
            "FROM   telemetry_audit_log "
            "WHERE  action_taken = 'HIGH_LOCKDOWN' "
            "ORDER  BY id DESC LIMIT 1"
        ).fetchone()

        if high_row:
            print(f"\n  SAMPLE HIGH_LOCKDOWN FEATURE SNAPSHOT")
            rule()
            pad("transaction_id",   high_row["transaction_id"])
            pad("predicted_score",  f"{high_row['predicted_risk_score']:.4f}")
            pad("final_resolution", high_row["final_resolution"])
            pad("timestamp_utc",    high_row["timestamp_utc"])
            print()
            try:
                snap = json.loads(high_row["feature_snapshot"])
                # Group features into logical clusters for readability
                clusters = {
                    "Identity & Device": [
                        "is_historical_payee", "is_vm_or_emulator",
                        "webdriver_detected",  "is_known_location",
                        "used_fido_passkey",   "ip_asn_type_encoded",
                    ],
                    "Session Behaviour": [
                        "profile_updated_this_session", "timezone_mismatch",
                        "pages_visited_pre_transfer",   "time_login_to_transfer_sec",
                        "form_completion_time_sec",     "password_entry_ms",
                    ],
                    "Biometrics": [
                        "mouse_linearity_score", "typing_cadence_score",
                        "bot_agility_index",
                    ],
                    "Network & Routing": [
                        "is_neobank_routing", "payee_account_age_hours",
                    ],
                    "Overlay / Takeover Signals": [
                        "is_in_active_call",          "is_screensharing_active",
                        "remote_access_app_detected", "coached_fraud_index",
                    ],
                    "Risk Composites": [
                        "trust_score_live",   "session_tension",
                        "mule_potential",     "transfer_intensity",
                        "transfer_amount_lek","transfers_past_24h",
                    ],
                }
                for cluster_name, keys in clusters.items():
                    print(f"  [{cluster_name}]")
                    for k in keys:
                        if k in snap:
                            print(f"    {k:<35} : {snap[k]}")
                # Any remaining keys not in clusters
                shown = {k for keys in clusters.values() for k in keys}
                extra = {k: v for k, v in snap.items() if k not in shown}
                if extra:
                    print(f"  [Other]")
                    for k, v in extra.items():
                        print(f"    {k:<35} : {v}")
            except Exception as parse_err:
                print(f"  (parse error: {parse_err})")
                print(f"  raw: {high_row['feature_snapshot'][:400]}")

        conn.close()

    except Exception as db_err:
        print(f"  DB ERROR: {db_err}")

    # ── Final verdict ─────────────────────────────────────────────────────────
    print()
    sep()
    if not errors:
        print(f"  RESULT : ALL {NUM_REQUESTS} TRANSACTIONS PROCESSED -- ZERO ERRORS")
    else:
        pct_ok = len(ok) / NUM_REQUESTS * 100
        print(f"  RESULT : {len(ok)}/{NUM_REQUESTS} OK  ({pct_ok:.1f}%)  |  {len(errors)} errors")
    sep()
    print()


if __name__ == "__main__":
    main()
