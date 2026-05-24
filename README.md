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


🛠️ Tech Stack & Design ChoicesThe prototype intentionally rejects heavyweight enterprise frameworks (like React, Docker, or distributed microservices) to optimize for raw speed, minimal network serialization overhead, and localized state execution.Backend: Python 3.x / Flask (Lightweight, low-overhead REST API routing)Machine Learning: XGBoost Classifier via scikit-learn & serialized with joblibDatabase Layer: SQLite (db.sqlite3) (Zero-latency localized relational state)Frontend: Vanilla JavaScript (ES2020) & Custom CSS3 (Zero build pipelines, instantaneous asset delivery tailored to Fibank's UI)📊 The 27 Behavioral SignalsTelemetry is grouped into four distinct layers of situational intelligence to feed the XGBoost model vector:I. Device & Environment Intelligencedevice_id / os_type / browser_type: Fingerprinting environments.is_vm_or_emulator: Detection of server racks or virtual sandboxes.timezone_mismatch: Real-time cross-referencing of browser local time against incoming GeoIP location.II. Interaction & Behavioral Biometricstyping_cadence_score: Micro-variations in keystroke flight and dwell intervals to identify non-human or scripted entries.mouse_linearity_score: Mathematical line analysis distinguishing curved human pointer movements from linear bot vectors.password_paste_detected: Binary indicator capturing programmatic clipboard injection instead of manual entry.III. Transaction Contexttransfer_amount_lek: Anomaly scaling against user baseline limits.payee_account_age_hours: Immediate flagging of newly created money-mule accounts.is_neobank_routing: Routing assessment on digital-only beneficiary accounts.IV. Sociotechnical & Social Engineering Intelligenceis_call_active / call_overlap_transfer: Monitors native hardware state to detect active voice calls during transactions.screen_recording_detected / remote_access_app_detected: Flags active media projection hooks (AnyDesk, TeamViewer overlays).🔐 Three-Tier Risk InterventionSecurity scales dynamically to meet the calculated session threat level, introducing friction only when a risk anomaly is detected.Risk StateScore WindowAction TriggeredEnd-User Experience🟢 LOW0.00 – 2.99Passive TrustFrictionless processing; transfer executes instantly with UI success state.🟡 MEDIUM3.00 – 6.99Biometric Step-UpStandard flow is paused. Triggers WebAuthn/Passkey or native biometric floor (Face ID/Touch ID) to satisfy EU PSD2 inherence criteria.🔴 HIGH7.00 – 10.00Hardware LockdownFull-screen app shielding. Demands physical FIDO2 security key insertion. Successfully isolates and neutralizes remote vishing threats.🚀 Getting StartedPrerequisitesPython 3.8 or higherpip (Python package manager)Installation & Local SetupClone the repository:Bashgit clone https://github.com/yourusername/fibank-fraud-detection.git
cd fibank-fraud-detection
Create and activate a virtual environment:
Bashpython -m venv venv
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
Install dependencies:Bashpip install -r requirements.txt
Initialize the Database:Ensure the localized SQLite schema is mapped by running the database build script:Bashpython init_db.py
Launch the Application:Bashpython app.py
Navigate to http://127.0.0.1:5000 in your browser to view the interactive dashboard and threat injector control panel.📜 Regulatory Alignment & CompliancePSD2 / Regulatory Technical Standards (RTS): Aligns completely with Article 97 and 18 criteria for Strong Customer Authentication (SCA) via real-time transaction monitoring.GDPR: Behavioral biometrics (keystroke flight, mouse paths) are completely evaluated in volatile memory during the active session. Raw coordinate/timing arrays are dropped post-inference and never saved to the relational database to ensure privacy compliance.Bank of Albania: Fully respects the native Regulation on Electronic Payment Instruments by providing strict, auditable transaction trails and human-explainable decision paths for frozen transactions.📄 LicenseThis project is licensed under the MIT License - see the LICENSE file for complete details.
