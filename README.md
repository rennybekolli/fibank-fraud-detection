# 🛡️ Fibank Intelligent Fraud Detection System (16 HR CIT Hackathon)


Presentation Link
(https://docs.google.com/presentation/d/1E_EcnwIikkUiIqFsS3_Bg0HFS0AoRGoGr5SslYc3UbM/edit?slide=id.p3#slide=id.p3)

### Real-Time Behavioural Risk Engine for Digital Banking

**Award-Winning Hackathon Submission · Tirana, Albania**

The **Fibank Intelligent Fraud Detection System** is a lightweight, high-performance behavioural risk engine designed for real-time fraud prevention in digital banking environments.

The platform is specifically engineered to detect and intercept:

* Authorised Push Payment (APP) fraud
* Social engineering attacks (vishing/coaching)
* Remote desktop account takeovers
* Automated bot-driven transactions

Unlike traditional fraud systems that rely heavily on static identifiers such as passwords or IP addresses, this platform continuously evaluates live behavioural telemetry throughout the user session.

Micro-interactions, device signatures, environmental anomalies, and hardware-level indicators are transformed into a continuously evolving behavioural trust profile before funds leave the account.

---

# 🏗️ System Architecture

The platform operates on a dual-engine architecture optimized for:

* Sub-millisecond inference
* Deterministic reliability
* Minimal processing overhead
* Real-time adaptive intervention

Incoming telemetry is processed simultaneously through:

1. A deterministic rule engine
2. A machine-learning scoring engine

```text
              [User Web Interface]
                       │
             (JSON Telemetry Stream)
                       │
                       ▼
               [Flask API Endpoint]
                       │
        ┌──────────────┴──────────────┐
        ▼                             ▼
[Deterministic Rule Engine]   [XGBoost Scoring Engine]
 (Fast branching logic)        (27D Feature Vector)
        │                             │
        └──────────────┬──────────────┘
                       ▼
             [Continuous Trust Score]
                       │
    ┌──────────────────┼──────────────────┐
    ▼                  ▼                  ▼

 🟢 LOW RISK      🟡 MEDIUM RISK      🔴 HIGH RISK
   Score < 3.0      Score 3.0–6.99      Score ≥ 7.0

 Frictionless       Biometric           FIDO2 Hardware
    Approval          Step-Up               Lockdown
```

---

# 🛠️ Tech Stack & Design Choices

The prototype intentionally avoids heavyweight enterprise frameworks such as React, Docker, or distributed microservices in order to prioritize:

* Raw execution speed
* Minimal serialization overhead
* Localized state execution
* Reduced latency
* Instant UI responsiveness

---

## Backend

* **Python 3.x**
* **Flask** — lightweight REST API routing with minimal overhead

---

## Machine Learning

* **XGBoost Classifier**
* Built with `scikit-learn`
* Serialized using `joblib`

---

## Database Layer

* **SQLite (`db.sqlite3`)**
* Localized relational state storage with near-zero operational overhead

---

## Frontend

* **Vanilla JavaScript (ES2020)**
* **Custom CSS3**
* No frontend frameworks
* No build pipelines
* Instant asset delivery

---

# 📊 Behavioural Intelligence Signals

Telemetry is grouped into four layers of situational intelligence that collectively feed the XGBoost feature vector.

---

## I. Device & Environment Intelligence

### `device_id / os_type / browser_type`

Environment fingerprinting and client profiling.

### `is_vm_or_emulator`

Detection of virtualized environments, sandbox execution layers, or server-rack activity.

### `timezone_mismatch`

Compares browser-local timezone against incoming GeoIP origin.

---

## II. Interaction & Behavioural Biometrics

### `typing_cadence_score`

Analyzes keystroke flight and dwell timing to distinguish organic human input from scripted automation.

### `mouse_linearity_score`

Uses path curvature analysis to separate natural pointer movement from linear bot trajectories.

### `password_paste_detected`

Flags clipboard-based credential injection instead of manual typing.

---

## III. Transaction Context Intelligence

### `transfer_amount_lek`

Detects anomalous transfer amounts against historical user baselines.

### `payee_account_age_hours`

Flags newly created beneficiary accounts commonly associated with money-mule activity.

### `is_neobank_routing`

Assesses digital-only routing destinations and elevated-risk transfer corridors.

---

## IV. Sociotechnical & Social Engineering Intelligence

### `is_call_active / call_overlap_transfer`

Detects active phone calls occurring during sensitive transfer activity.

### `screen_recording_detected / remote_access_app_detected`

Identifies screen-sharing hooks and remote-control overlays such as:

* AnyDesk
* TeamViewer
* Remote desktop tooling

---

# 🔐 Three-Tier Risk Intervention

Security intervention dynamically escalates based on the calculated trust score.

Friction is introduced only when anomalous behaviour is detected.

| Risk Level    | Score Range    | Action            | User Experience                                                     |
| ------------- | -------------- | ----------------- | ------------------------------------------------------------------- |
| 🟢 **LOW**    | `0.00 – 2.99`  | Passive Trust     | Instant frictionless transaction approval                           |
| 🟡 **MEDIUM** | `3.00 – 6.99`  | Biometric Step-Up | WebAuthn / Passkey / Face ID / Touch ID verification                |
| 🔴 **HIGH**   | `7.00 – 10.00` | Hardware Lockdown | Requires physical FIDO2 security key and activates secure shielding |

---

# 🚀 Getting Started

## Prerequisites

* Python 3.8+
* `pip` package manager

---

# Installation & Local Setup

## Clone the Repository

```bash
git clone <repository-url>
cd fibank-fraud-detection
```

---

## Create a Virtual Environment

```bash
python -m venv venv
```

### Windows

```bash
venv\Scripts\activate
```

### macOS / Linux

```bash
source venv/bin/activate
```

---

## Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Initialize the Database

```bash
python init_db.py
```

Initializes and maps the localized SQLite schema.

---

## Launch the Application

```bash
python app.py
```

Open your browser and navigate to:

```text
http://127.0.0.1:5000
```

You will be presented with the interactive dashboard and threat-injection control panel.

---

# 📜 Regulatory Alignment & Compliance

## PSD2 / RTS Compliance

Aligned with Articles 97 and 18 of the PSD2 Regulatory Technical Standards for Strong Customer Authentication (SCA).

The system implements:

* Real-time transaction monitoring
* Adaptive authentication escalation
* Behaviour-driven trust evaluation

---

## GDPR Compliance

Behavioural biometric telemetry such as:

* Keystroke timing
* Pointer trajectories
* Interaction cadence

is processed exclusively in volatile memory during active sessions.

Raw telemetry arrays are discarded immediately after inference and are never persisted to the relational database.

---

## Bank of Albania Compliance

Designed to comply with the Regulation on Electronic Payment Instruments through:

* Auditable transaction trails
* Human-explainable fraud scoring
* Transparent intervention pathways
* Reviewable frozen-transaction logic

---

# 📄 License

This project is licensed under the:

* MIT License
* FiBank Internal Evaluation License
* Team Ace Hackathon License

See the `LICENSE` file for complete details.
