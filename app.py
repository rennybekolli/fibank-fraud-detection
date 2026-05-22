import json
import os
import random
import sqlite3
import time
import warnings
from datetime import datetime, timedelta

import joblib
import numpy as np
from flask import Flask, jsonify, render_template, request, session

warnings.filterwarnings("ignore")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "fibank_hackathon_2024_xK9mP2qr")

# ── Model loading ──────────────────────────────────────────────────────────────
MODEL = joblib.load("best_fraud_engine.joblib")
_ENCODERS = joblib.load("label_encoder.joblib")
IP_ASN_ENCODER = _ENCODERS["ip_asn_type"]

FEATURE_ORDER = [
    "is_historical_payee", "is_vm_or_emulator", "webdriver_detected",
    "is_known_location", "profile_updated_this_session", "timezone_mismatch",
    "pages_visited_pre_transfer", "time_login_to_transfer_sec", "used_fido_passkey",
    "form_completion_time_sec", "password_entry_ms", "mouse_linearity_score",
    "typing_cadence_score", "is_neobank_routing", "payee_account_age_hours",
    "is_in_active_call", "is_screensharing_active", "remote_access_app_detected",
    "trust_score_live", "session_tension", "coached_fraud_index", "mule_potential",
    "bot_agility_index", "transfer_intensity", "transfer_amount_lek",
    "transfers_past_24h", "ip_asn_type_encoded",
]

DB_PATH = "db.sqlite3"


# ── Database ───────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, name TEXT, iban TEXT,
        balance REAL, location TEXT, account_age_days INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, amount REAL, recipient_name TEXT, recipient_iban TEXT,
        timestamp TEXT, risk_score REAL, risk_level TEXT,
        triggered_signals TEXT, status TEXT
    )""")
    if not c.execute("SELECT 1 FROM users WHERE id=1").fetchone():
        c.execute(
            "INSERT INTO users VALUES (1,'Ardi Berisha',"
            "'AL47 2121 1009 0000 0002 3569 8741',450000,'Tirana, Albania',847)"
        )
    if not c.execute("SELECT 1 FROM transactions WHERE user_id=1").fetchone():
        now = datetime.now()
        seed = [
            (1, 5000, "Elton Hoxha", "AL35 2021 1109 0000 0000 2356 8762",
             (now - timedelta(days=3)).isoformat(), 1.2, "low",
             json.dumps(["No anomalies detected"]), "completed"),
            (1, 85000, "Rinas Logistics SH.P.K", "AL47 2121 1009 0000 0000 1234 5678",
             (now - timedelta(days=12)).isoformat(), 4.7, "medium",
             json.dumps(["Timezone mismatch", "Neobank routing detected"]), "completed"),
            (1, 210000, "Unknown Recipient", "AL47 9999 0000 0000 0000 0000 0001",
             (now - timedelta(days=30)).isoformat(), 8.9, "high",
             json.dumps(["Virtual machine detected", "Remote access software detected",
                         "Coached fraud indicators"]), "blocked"),
        ]
        c.executemany(
            "INSERT INTO transactions (user_id,amount,recipient_name,recipient_iban,"
            "timestamp,risk_score,risk_level,triggered_signals,status) "
            "VALUES (?,?,?,?,?,?,?,?,?)", seed
        )
    conn.commit()
    conn.close()


# ── Presenter signal defaults ──────────────────────────────────────────────────
PRESENTER_DEFAULTS = {
    "is_historical_payee": 1,
    "is_known_location": 1,
    "used_fido_passkey": 1,
    "ip_asn_type": "residential",
    "timezone_mismatch": 0,
    "mouse_linearity_score": 0.1,
    "typing_cadence_score": 0.1,
    "is_neobank_routing": 0,
    "mule_potential": 0,
    "session_tension": 0,
    "is_vm_or_emulator": 0,
    "webdriver_detected": 0,
    "bot_agility_index": 0,
    "is_in_active_call": 0,
    "is_screensharing_active": 0,
    "remote_access_app_detected": 0,
    "coached_fraud_index": 0,
}


def presenter_signals():
    return {**PRESENTER_DEFAULTS, **session.get("presenter_signals", {})}


# ── Feature helpers ────────────────────────────────────────────────────────────
def _trust_score(conn, account_age_days):
    rows = conn.execute(
        'SELECT risk_score FROM transactions WHERE user_id=1 AND status="completed"'
    ).fetchall()
    avg_risk = (sum(r[0] for r in rows) / len(rows)) if rows else 3.0
    age_factor = min(10.0, account_age_days / 90.0)
    return round(age_factor * (1.0 - avg_risk / 10.0), 3)


def _transfer_intensity(conn, amount):
    rows = conn.execute(
        'SELECT amount FROM transactions WHERE user_id=1 AND status="completed"'
    ).fetchall()
    if rows:
        avg = sum(r[0] for r in rows) / len(rows)
        return round(float(amount) / max(avg, 1.0), 3)
    return 1.0


def build_feature_vector(data, conn):
    ps = presenter_signals()
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()

    is_historical = int(ps["is_historical_payee"])
    payee_age = 8760.0 if is_historical else float(random.randint(2, 48))
    amount = float(data.get("amount", 10000))

    since_24h = (datetime.now() - timedelta(hours=24)).isoformat()
    txns_24h = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id=1 AND timestamp>?", (since_24h,)
    ).fetchone()[0]

    ip_str = ps.get("ip_asn_type", "residential")
    try:
        ip_enc = int(IP_ASN_ENCODER.transform([ip_str])[0])
    except Exception:
        ip_enc = int(IP_ASN_ENCODER.transform(["residential"])[0])

    login_time = session.get("login_time", time.time())
    return {
        "is_historical_payee":          is_historical,
        "is_vm_or_emulator":            int(ps["is_vm_or_emulator"]),
        "webdriver_detected":           int(ps["webdriver_detected"]),
        "is_known_location":            int(ps["is_known_location"]),
        "profile_updated_this_session": int(session.get("profile_updated", 0)),
        "timezone_mismatch":            int(ps["timezone_mismatch"]),
        "pages_visited_pre_transfer":   int(data.get("pages_visited_pre_transfer", 1)),
        "time_login_to_transfer_sec":   max(38.0, min(55.0, float(data.get(
            "time_login_to_transfer_sec", time.time() - login_time)))),
        "used_fido_passkey":            int(ps["used_fido_passkey"]),
        "form_completion_time_sec":     float(data.get("form_completion_time_sec", 15.0)),
        "password_entry_ms":            float(data.get("password_entry_ms", 2000.0)),
        "mouse_linearity_score":        float(ps["mouse_linearity_score"]),
        "typing_cadence_score":         float(ps["typing_cadence_score"]),
        "is_neobank_routing":           int(ps["is_neobank_routing"]),
        "payee_account_age_hours":      payee_age,
        "is_in_active_call":            int(ps["is_in_active_call"]),
        "is_screensharing_active":      int(ps["is_screensharing_active"]),
        "remote_access_app_detected":   int(ps["remote_access_app_detected"]),
        "trust_score_live":             _trust_score(conn, user["account_age_days"]),
        "session_tension":              float(ps["session_tension"]),
        "coached_fraud_index":          float(ps["coached_fraud_index"]),
        "mule_potential":               float(ps["mule_potential"]),
        "bot_agility_index":            float(ps["bot_agility_index"]),
        "transfer_intensity":           _transfer_intensity(conn, amount),
        "transfer_amount_lek":          amount,
        "transfers_past_24h":           int(txns_24h),
        "ip_asn_type_encoded":          ip_enc,
    }


def triggered_signals_list(feats, ps):
    s = []
    if not feats["is_historical_payee"]:          s.append("Unknown payee")
    if feats["is_vm_or_emulator"]:                s.append("Virtual machine detected")
    if feats["webdriver_detected"]:               s.append("Browser automation detected")
    if not feats["is_known_location"]:            s.append("Unknown login location")
    if feats["profile_updated_this_session"]:     s.append("Profile changed this session")
    if feats["timezone_mismatch"]:                s.append("Timezone mismatch")
    if feats["mouse_linearity_score"] > 0.5:      s.append("Robotic mouse movement")
    if feats["typing_cadence_score"] > 0.5:       s.append("Abnormal typing pattern")
    if feats["is_neobank_routing"]:               s.append("Neobank routing detected")
    if feats["is_in_active_call"]:                s.append("Active phone call detected")
    if feats["is_screensharing_active"]:          s.append("Screen sharing active")
    if feats["remote_access_app_detected"]:       s.append("Remote access software detected")
    if feats["coached_fraud_index"] > 0:          s.append("Coached fraud indicators")
    if feats["mule_potential"] > 0:               s.append("Potential money mule pattern")
    if feats["bot_agility_index"] > 0:            s.append("Bot-like agility detected")
    if feats["transfer_intensity"] > 5:           s.append("Unusually large transfer")
    ip_asn = ps.get("ip_asn_type", "residential")
    if ip_asn in ("hosting", "business"):         s.append(f"Suspicious IP origin ({ip_asn})")
    return s or ["No anomalies detected"]


def generate_explanation(risk_level, triggered_signals):
    clean = [s for s in triggered_signals if s != "No anomalies detected"]
    if risk_level == "high":
        return (
            "Multiple critical threat signals were detected simultaneously including: "
            + ", ".join(clean) + ". "
            "This transfer has been immediately blocked for your protection."
        )
    if risk_level == "medium":
        return (
            "Unusual patterns were detected in this session: "
            + ", ".join(clean) + ". "
            "Please verify your identity to continue."
        )
    return ""


def score_features(feats):
    X = np.array([[feats[f] for f in FEATURE_ORDER]])
    prob = float(MODEL.predict_proba(X)[0][1])
    risk_score = round(prob * 10, 2)
    if risk_score >= 7.0:
        risk_level = "high"
    elif risk_score >= 3.0:
        risk_level = "medium"
    else:
        risk_level = "low"
    return risk_score, risk_level


# ── Middleware ─────────────────────────────────────────────────────────────────
@app.before_request
def ensure_login_time():
    if "login_time" not in session:
        session["login_time"] = time.time()


# ── Page routes ────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/presenter")
def presenter():
    return render_template("presenter.html")


@app.route("/profile")
def profile():
    return render_template("profile.html")


# ── API routes ─────────────────────────────────────────────────────────────────
@app.route("/api/user")
def api_user():
    conn = get_db()
    user = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    txns = [dict(r) for r in conn.execute(
        "SELECT * FROM transactions WHERE user_id=1 ORDER BY timestamp DESC"
    ).fetchall()]
    for t in txns:
        t["triggered_signals"] = json.loads(t["triggered_signals"])
    conn.close()
    return jsonify({
        "user": user,
        "transactions": txns,
        "profile_updated": session.get("profile_updated", 0),
        "password_pasted": session.get("password_pasted", 0),
    })


@app.route("/api/score", methods=["POST"])
def api_score():
    data = request.get_json(force=True) or {}
    conn = get_db()
    ps = presenter_signals()
    feats = build_feature_vector(data, conn)
    conn.close()

    risk_score, risk_level = score_features(feats)
    signals = triggered_signals_list(feats, ps)
    explanation = generate_explanation(risk_level, signals)

    return jsonify({
        "risk_score": risk_score,
        "risk_level": risk_level,
        "explanation": explanation,
        "triggered_signals": signals,
        "features": feats,
    })


@app.route("/api/transfer", methods=["POST"])
def api_transfer():
    data = request.get_json(force=True) or {}
    amount = float(data.get("amount", 0))
    status = data.get("status", "pending")

    conn = get_db()
    conn.execute(
        "INSERT INTO transactions (user_id,amount,recipient_name,recipient_iban,"
        "timestamp,risk_score,risk_level,triggered_signals,status) "
        "VALUES (1,?,?,?,?,?,?,?,?)",
        (amount, data.get("recipient_name", ""), data.get("recipient_iban", ""),
         datetime.now().isoformat(),
         float(data.get("risk_score", 0)), data.get("risk_level", "low"),
         json.dumps(data.get("triggered_signals", [])), status)
    )
    new_balance = None
    if status == "completed":
        conn.execute("UPDATE users SET balance=balance-? WHERE id=1", (amount,))
        new_balance = float(conn.execute(
            "SELECT balance FROM users WHERE id=1"
        ).fetchone()[0])
    conn.commit()
    conn.close()
    return jsonify({"success": True, "new_balance": new_balance})


@app.route("/api/update-profile", methods=["POST"])
def api_update_profile():
    data = request.get_json(force=True) or {}
    conn = get_db()
    if data.get("name"):
        conn.execute("UPDATE users SET name=? WHERE id=1", (data["name"],))
    if data.get("location"):
        conn.execute("UPDATE users SET location=? WHERE id=1", (data["location"],))
    conn.commit()
    conn.close()
    session["profile_updated"] = 1
    return jsonify({"success": True})


@app.route("/api/presenter/set-signals", methods=["POST"])
def api_set_signals():
    data = request.get_json(force=True) or {}
    session["presenter_signals"] = data
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/report-paste", methods=["POST"])
def api_report_paste():
    session["password_pasted"] = 1
    return jsonify({"success": True})


@app.route("/api/reset-session")
def api_reset_session():
    session.pop("presenter_signals", None)
    session.pop("profile_updated", None)
    session.pop("password_pasted", None)
    return jsonify({"success": True})


@app.route("/api/session-status")
def api_session_status():
    return jsonify({
        "profile_updated": session.get("profile_updated", 0),
        "password_pasted": session.get("password_pasted", 0),
        "presenter_signals": presenter_signals(),
    })


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
