🛡️ Fibank Intelligent Fraud Detection SystemA Real-Time Behavioural Risk Engine for Digital Banking Award-Winning Hackathon Submission · Tirana, Albania
The Fibank Intelligent Fraud Detection System is a lightweight, high-performance, real-time multi-signal behavioral risk engine. 
It is explicitly engineered to intercept Authorised Push Payment (APP) fraud, social engineering (vishing/coaching), remote-desktop takeovers, and automated bot-driven transfers before 
funds leave the bank account.Instead of evaluating a transaction solely by static data points (such as login credentials or fixed geographic IPs), 
this system monitors session-wide user interaction telemetry—transforming micro-movements, device signatures, and hardware API indicators into a live behavioral fingerprint.
🏗️ System ArchitectureThe system operates on a dual-engine architecture designed for sub-millisecond inference and absolute reliability. 
It processes incoming telemetry via parallel paths: a high-speed deterministic rule engine and a gradient-boosted machine learning model.

              [User Web Interface] ──(JSON Telemetry)──> [Flask API Endpoint]
                                                 │
                        ┌────────────────────────┴────────────────────────┐
                        ▼                                                 ▼
          [Deterministic Rule Engine]                          [XGBoost Scoring Engine]
         (High-speed branching logic)                         (27-Dimension Feature Vector)
                        │                                                 │
                        └────────────────────────┬────────────────────────┘
                                                 ▼
                                     [Continuous Trust Score]
                                                 │
         ┌───────────────────────────────────────┼───────────────────────────────────────┐
         ▼                                       ▼                                       ▼
    🟢 LOW RISK                             🟡 MEDIUM RISK                           🔴 HIGH RISK
   Score < 3.0                              Score 3.0 - 6.99                         Score ≥ 7.0
(Frictionless Path)                      (Biometric Step-Up)                     (FIDO Hardware Lock)


🛠️ Tech Stack & Design Choices

The prototype intentionally avoids heavyweight enterprise frameworks such as React, Docker, or distributed microservices in order to optimize for:

Raw execution speed
Minimal network serialization overhead
Localized state processing
Instantaneous UI responsiveness
Backend
Python 3.x
Flask — lightweight, low-overhead REST API routing
Machine Learning
XGBoost Classifier via scikit-learn
Serialized using joblib
Database Layer
SQLite (db.sqlite3)
Zero-latency localized relational state storage
Frontend
Vanilla JavaScript (ES2020)
Custom CSS3
No build pipelines or frontend abstraction layers
📊 The 27 Behavioral Signals

Telemetry is grouped into four distinct layers of situational intelligence that feed the XGBoost inference vector.

I. Device & Environment Intelligence
device_id / os_type / browser_type

Environment fingerprinting and client profiling.

is_vm_or_emulator

Detection of virtualized environments, server racks, or sandbox execution layers.

timezone_mismatch

Real-time comparison between browser-local timezone and incoming GeoIP region.

II. Interaction & Behavioral Biometrics
typing_cadence_score

Micro-variations in keystroke flight and dwell timing used to distinguish humans from automated scripts.

mouse_linearity_score

Mathematical path analysis separating organic curved pointer movement from bot-like linear vectors.

password_paste_detected

Binary signal identifying clipboard injection instead of manual credential entry.

III. Transaction Context Intelligence
transfer_amount_lek

Anomaly scaling against historical user transfer baselines.

payee_account_age_hours

Flags newly created beneficiary or potential money-mule accounts.

is_neobank_routing

Routing assessment for digital-only banking destinations.

IV. Sociotechnical & Social Engineering Intelligence
is_call_active / call_overlap_transfer

Monitors native hardware state to identify active phone calls during fund transfers.

screen_recording_detected / remote_access_app_detected

Detects media projection hooks and remote-control overlays such as AnyDesk or TeamViewer.

🔐 Three-Tier Risk Intervention

Security dynamically escalates based on the calculated session threat level.
Friction is introduced only when anomalous behavior is detected.

Risk State	Score Window	Action Triggered	End-User Experience
🟢 LOW	0.00 – 2.99	Passive Trust	Transfer executes instantly with frictionless processing
🟡 MEDIUM	3.00 – 6.99	Biometric Step-Up	Triggers WebAuthn/Passkey or native biometrics (Face ID / Touch ID)
🔴 HIGH	7.00 – 10.00	Hardware Lockdown	Requires physical FIDO2 security key and activates full-screen shielding

🚀 Getting Started

Prerequisites
Python 3.8 or higher
pip (Python package manager)
Installation & Local Setup

Clone the Repository
git clone <repository-url>
cd fibank-fraud-detection

Create and Activate a Virtual Environment
python -m venv venv
Windows
venv\Scripts\activate
macOS / Linux
source venv/bin/activate

Install Dependencies
pip install -r requirements.txt

Initialize the Database
python init_db.py

This maps the localized SQLite schema.

Launch the Application
python app.py

Open your browser and navigate to:

http://127.0.0.1:5000

You will see the interactive dashboard and threat injector control panel.

📜 Regulatory Alignment & Compliance
PSD2 / Regulatory Technical Standards (RTS)

Aligns with Articles 97 and 18 requirements for Strong Customer Authentication (SCA) through real-time transaction monitoring and adaptive authentication flows.

GDPR

Behavioral biometrics such as keystroke timing and mouse trajectories are processed exclusively in volatile memory during active sessions.

Raw coordinate arrays and timing vectors are discarded immediately after inference and are never persisted to the relational database.

Bank of Albania Compliance

Respects the Regulation on Electronic Payment Instruments through:

Auditable transaction trails
Human-explainable fraud decisions
Transparent intervention logic for frozen or challenged transactions
📄 License

This project is licensed under the MIT / FiBank / Team Ace License.
See the LICENSE file for complete details.
