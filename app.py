import json
import os
import random
import sqlite3
import time
import warnings
from datetime import datetime, timedelta

import joblib
import numpy as np
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

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


SEED_TRANSACTIONS = lambda now: [
    (1, 5000,   "Elton Hoxha",           "AL35 2021 1109 0000 0000 2356 8762",
     (now - timedelta(days=3)).isoformat(),  1.2, "low",
     json.dumps(["No anomalies detected"]), "completed"),
    (1, 85000,  "Rinas Logistics SH.P.K", "AL47 2121 1009 0000 0000 1234 5678",
     (now - timedelta(days=12)).isoformat(), 4.7, "medium",
     json.dumps(["Timezone mismatch", "Neobank routing detected"]), "completed"),
    (1, 210000, "Unknown Recipient",       "AL47 9999 0000 0000 0000 0000 0001",
     (now - timedelta(days=30)).isoformat(), 8.9, "high",
     json.dumps(["Virtual machine detected", "Remote access software detected",
                 "Coached fraud indicators"]), "blocked"),
]

TXN_INSERT_SQL = (
    "INSERT INTO transactions (user_id,amount,recipient_name,recipient_iban,"
    "timestamp,risk_score,risk_level,triggered_signals,status) "
    "VALUES (?,?,?,?,?,?,?,?,?)"
)


def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── users table (with password) ────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY, name TEXT, iban TEXT, balance REAL,
        location TEXT, account_age_days INTEGER, password TEXT DEFAULT 'admin'
    )""")
    # Migration: add password column to existing DBs that lack it
    try:
        c.execute("ALTER TABLE users ADD COLUMN password TEXT DEFAULT 'admin'")
    except Exception:
        pass  # column already exists

    # ── transactions table ─────────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, amount REAL, recipient_name TEXT, recipient_iban TEXT,
        timestamp TEXT, risk_score REAL, risk_level TEXT,
        triggered_signals TEXT, status TEXT
    )""")

    # ── saved_recipients table ─────────────────────────────────────────────────
    c.execute("""CREATE TABLE IF NOT EXISTS saved_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, name TEXT, iban TEXT
    )""")

    # ── Seed user ──────────────────────────────────────────────────────────────
    if not c.execute("SELECT 1 FROM users WHERE id=1").fetchone():
        c.execute(
            "INSERT INTO users (id,name,iban,balance,location,account_age_days,password) "
            "VALUES (1,'Ardi Berisha','AL47 2121 1009 0000 0002 3569 8741',"
            "450000,'Tirana, Albania',847,'admin')"
        )
    else:
        # Ensure password is populated on existing rows
        c.execute(
            "UPDATE users SET password='admin' WHERE id=1 AND (password IS NULL OR password='')"
        )

    # ── Seed saved recipients ──────────────────────────────────────────────────
    if not c.execute("SELECT 1 FROM saved_recipients WHERE user_id=1").fetchone():
        c.executemany(
            "INSERT INTO saved_recipients (user_id, name, iban) VALUES (?,?,?)",
            [
                (1, "Elton Hoxha",            "AL35 2021 1109 0000 0000 2356 8762"),
                (1, "Rinas Logistics SH.P.K", "AL47 2121 1009 0000 0000 1234 5678"),
            ]
        )

    # ── Seed transactions ──────────────────────────────────────────────────────
    if not c.execute("SELECT 1 FROM transactions WHERE user_id=1").fetchone():
        c.executemany(TXN_INSERT_SQL, SEED_TRANSACTIONS(datetime.now()))

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
    # mule_potential and session_tension kept at 0 for XGBoost model compat
    "mule_potential": 0,
    "session_tension": 0,
    "is_vm_or_emulator": 0,
    "webdriver_detected": 0,
    "bot_agility_index": 0,
    "is_in_active_call": 0,
    "is_screensharing_active": 0,
    "remote_access_app_detected": 0,
    "coached_fraud_index": 0,
    # NEW HIGH signal
    "session_from_link": 0,
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
    # session_from_link: either presenter toggled OR auto-detected via referrer/URL
    session_from_link = max(
        int(ps.get("session_from_link", 0)),
        int(session.get("session_from_link", 0)),
    )

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
        "session_tension":              float(ps.get("session_tension", 0)),   # always 0, kept for XGBoost
        "coached_fraud_index":          float(ps["coached_fraud_index"]),
        "mule_potential":               float(ps.get("mule_potential", 0)),    # always 0, kept for XGBoost
        "bot_agility_index":            float(ps["bot_agility_index"]),
        "transfer_intensity":           _transfer_intensity(conn, amount),
        "transfer_amount_lek":          amount,
        "transfers_past_24h":           int(txns_24h),
        "ip_asn_type_encoded":          ip_enc,
        # Extended signals (not in XGBoost feature order, used only for rule engine)
        "session_from_link":            session_from_link,
    }


def triggered_signals_list(feats, ps, sess_signals=None):
    s = []
    sess_signals = sess_signals or {}
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
    if feats["bot_agility_index"] > 0:            s.append("Bot-like agility detected")
    if feats["transfer_intensity"] > 5:           s.append("Unusually large transfer")
    if feats.get("session_from_link"):            s.append("Session opened from external link")
    ip_asn = ps.get("ip_asn_type", "residential")
    if ip_asn in ("hosting", "business"):         s.append(f"Suspicious IP origin ({ip_asn})")
    # Auto-detected session signals
    if sess_signals.get("clipboard_activity"):    s.append("Clipboard activity detected")
    if sess_signals.get("tab_switched"):          s.append("Tab-switching detected")
    if sess_signals.get("unknown_iban"):          s.append("Unrecognised IBAN entered")
    if sess_signals.get("password_pasted"):       s.append("Password pasted")
    if sess_signals.get("new_recipient"):         s.append("New recipient added")
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
    """Keep XGBoost for the 'features' payload shown on the Profile signal breakdown."""
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


def calculate_risk_score(feats, amount, sess_signals):
    """
    Deterministic rule-based fraud score (0.0 – 10.0).

    Architecture:
      BASE 1.0
      + automated session additions (flat)
      + MEDIUM-tier signal sum × 1.5
      + HIGH-tier signal sum × 2.5
      → hard floor: if amount > 90 000 ALL, score = max(score, 3.5)
      → ×1.15 amount scalar if amount > 90 000 ALL
      → clamped [0, 10]

    Scenario calibration (10 000 ALL unless noted):
      S1 LOW  : all-safe defaults              → 1.0
      S2 MED  : tz + neobank + no-hist + no-fido + mouse 0.65, 85 000 ALL → ~4.3
      S3 HIGH : session_from_link + bot + screen + tz + mouse/typing 0.9  → ~7.2
    """
    score = 1.0  # BASE

    # ── Automated session signals (flat additions) ─────────────────────────────
    if sess_signals.get("password_pasted"):    score += 0.50
    if sess_signals.get("new_recipient"):      score += 0.50
    if sess_signals.get("profile_updated"):    score += 0.50
    if sess_signals.get("clipboard_activity"): score += 0.50
    if sess_signals.get("tab_switched"):       score += 0.40
    if sess_signals.get("unknown_iban"):       score += 0.60
    if amount > 90_000:                        score += 1.0

    # ── MEDIUM-tier manual signals (× 1.5) ────────────────────────────────────
    m = 0.0
    if feats["timezone_mismatch"]:                m += 0.50
    if feats["is_neobank_routing"]:               m += 0.50
    if not feats["is_historical_payee"]:          m += 0.50
    if not feats["used_fido_passkey"]:            m += 0.30
    if not feats["is_known_location"]:            m += 0.50
    if feats["mouse_linearity_score"] > 0.5:      m += 0.40
    if feats["typing_cadence_score"] > 0.5:       m += 0.40
    score += m * 1.5

    # ── HIGH-tier manual signals (× 2.5) ──────────────────────────────────────
    h = 0.0
    if feats.get("session_from_link"):            h += 0.80   # opened from email/SMS link
    if feats["bot_agility_index"]:                h += 0.50
    if feats["is_screensharing_active"]:          h += 0.40
    if feats["is_vm_or_emulator"]:                h += 0.40
    if feats["remote_access_app_detected"]:       h += 0.40
    if feats["is_in_active_call"]:                h += 0.30
    if feats["coached_fraud_index"]:              h += 0.30
    score += h * 2.5

    # ── Daily transfer limit hard floor (> 90 000 ALL always triggers MEDIUM) ─
    if amount > 90_000:
        score = max(score, 3.5)
        score *= 1.15

    return round(min(10.0, max(0.0, score)), 2)


# ── Auth helper ────────────────────────────────────────────────────────────────
def logged_in():
    return bool(session.get("user_id"))


# ── Middleware ─────────────────────────────────────────────────────────────────
@app.before_request
def ensure_login_time():
    if "login_time" not in session:
        session["login_time"] = time.time()


# ── Page routes ────────────────────────────────────────────────────────────────
@app.route("/")
def login_page():
    if logged_in():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/dashboard")
def dashboard():
    if not logged_in():
        return redirect(url_for("login_page"))
    return render_template("index.html")


@app.route("/presenter")
def presenter():
    return render_template("presenter.html")


@app.route("/profile")
def profile():
    if not logged_in():
        return redirect(url_for("login_page"))
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

    amount = float(data.get("amount", 10000))

    # Rule-based deterministic score (primary — drives UI flow)
    sess_signals = {
        "password_pasted":    session.get("password_pasted",    0),
        "new_recipient":      session.get("new_recipient",      0),
        "profile_updated":    session.get("profile_updated",    0),
        "clipboard_activity": session.get("clipboard_activity", 0),
        "tab_switched":       session.get("tab_switched",       0),
        "unknown_iban":       session.get("unknown_iban",       0),
    }
    risk_score = calculate_risk_score(feats, amount, sess_signals)
    if risk_score >= 7.0:
        risk_level = "high"
    elif risk_score >= 3.0:
        risk_level = "medium"
    else:
        risk_level = "low"

    signals = triggered_signals_list(feats, ps, sess_signals)
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
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/report-clipboard", methods=["POST"])
def api_report_clipboard():
    session["clipboard_activity"] = 1
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/report-tab-switch", methods=["POST"])
def api_report_tab_switch():
    session["tab_switched"] = 1
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/report-referrer", methods=["POST"])
def api_report_referrer():
    """Called when JS detects session opened from email/SMS/external link."""
    session["session_from_link"] = 1
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/report-unknown-iban", methods=["POST"])
def api_report_unknown_iban():
    session["unknown_iban"] = 1
    session.modified = True
    return jsonify({"success": True})


@app.route("/api/reset-session")
def api_reset_session():
    for key in ("presenter_signals", "profile_updated", "password_pasted",
                "new_recipient", "clipboard_activity", "tab_switched",
                "session_from_link", "unknown_iban"):
        session.pop(key, None)
    return jsonify({"success": True})


@app.route("/api/session-status")
def api_session_status():
    return jsonify({
        "profile_updated":    session.get("profile_updated",    0),
        "password_pasted":    session.get("password_pasted",    0),
        "new_recipient":      session.get("new_recipient",      0),
        "clipboard_activity": session.get("clipboard_activity", 0),
        "tab_switched":       session.get("tab_switched",       0),
        "session_from_link":  session.get("session_from_link",  0),
        "unknown_iban":       session.get("unknown_iban",       0),
        "presenter_signals":  presenter_signals(),
    })


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True) or {}
    username = data.get("username", "").strip().lower()
    password = data.get("password", "").strip()

    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    conn.close()

    # Accept "admin", the user's full name, or their IBAN (spaces stripped)
    valid = {
        "admin",
        user["name"].lower(),
        user["iban"].replace(" ", "").lower(),
    }
    if username in valid and password == user["password"]:
        session["user_id"] = 1
        session["login_time"] = time.time()   # restart fraud timer on login
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/recipients/add", methods=["POST"])
def api_recipients_add():
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    iban = data.get("iban", "").strip()
    if not name or not iban:
        return jsonify({"success": False, "error": "Name and IBAN required"}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO saved_recipients (user_id, name, iban) VALUES (1, ?, ?)", (name, iban)
    )
    row = dict(conn.execute(
        "SELECT id, name, iban FROM saved_recipients WHERE user_id=1 ORDER BY id DESC LIMIT 1"
    ).fetchone())
    conn.commit()
    conn.close()
    session["new_recipient"] = 1
    session.modified = True
    return jsonify({"success": True, "recipient": row})


@app.route("/api/recipients")
def api_recipients():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT id, name, iban FROM saved_recipients WHERE user_id=1 ORDER BY name"
    ).fetchall()]
    conn.close()
    return jsonify({"recipients": rows})


@app.route("/api/clear-history", methods=["POST"])
def api_clear_history():
    """Wipe all demo transactions and reset balance — presenter use only."""
    conn = get_db()
    conn.execute("DELETE FROM transactions WHERE user_id=1")
    conn.executemany(TXN_INSERT_SQL, SEED_TRANSACTIONS(datetime.now()))
    conn.execute("UPDATE users SET balance=450000, location='Tirana, Albania', name='Ardi Berisha' WHERE id=1")
    conn.commit()
    conn.close()

    # Also wipe session state so the presenter gauge resets cleanly
    for key in ("presenter_signals", "profile_updated", "password_pasted",
                "new_recipient", "clipboard_activity", "tab_switched",
                "session_from_link", "unknown_iban"):
        session.pop(key, None)

    return jsonify({"success": True})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5000)
