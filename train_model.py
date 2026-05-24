#!/usr/bin/env python3
"""
train_model.py  --  Fibank Fraud Detection * XGBoost Training Pipeline
======================================================================

Three core improvements over a naive XGBoost baseline:

1. DYNAMIC scale_pos_weight
   Computed per-fold from the TRAINING split only (no data leakage).
   Formula: n_negative / n_positive.
   With a typical 98:2 ratio this yields ~49, forcing XGBoost to treat
   every fraud sample as 49× more informative than a legitimate one.

2. CUSTOM EVALUATION METRIC  --  PR-AUC + F1-Score composite
   Standard accuracy is deceptive on 99:1 imbalance: a model that
   predicts "legitimate" for everything scores 99 % accuracy while
   catching exactly zero fraud cases.  We replace it with:

     • PR-AUC  -- area under the Precision-Recall curve; captures model
                 quality across the full threshold range, not just one
                 operating point.  Preferred over ROC-AUC when positives
                 are rare because it is sensitive to false negatives.

     • F1-Score -- harmonic mean of precision and recall at the threshold
                  that maximises F1 on the validation fold, subject to
                  recall >= MIN_RECALL.  The recall floor prevents the
                  model from achieving high precision by simply refusing
                  to flag borderline cases (which would miss real fraud).

     Combined score:  composite = α*PR-AUC + (1-α)*F1   (default α=0.60)

3. STRATIFIED K-FOLD CROSS-VALIDATION
   sklearn's StratifiedKFold preserves the fraud:legitimate ratio in
   EVERY fold -- both training and validation partitions.  Without
   stratification a random split on a 2% fraud dataset has a ~13 % chance
   of producing a fold whose validation set contains ZERO fraud samples,
   making the metric meaningless and the reported variance deceptive.

Usage:
  python train_model.py                       # 5-fold CV + retrain final model
  python train_model.py --folds 10            # 10-fold CV
  python train_model.py --samples 50000       # larger synthetic dataset
  python train_model.py --fraud-rate 0.01     # 1 % fraud (harder problem)
  python train_model.py --no-retrain          # CV only, skip .joblib writes
  python train_model.py --alpha 0.7           # weight PR-AUC more heavily
"""

import argparse
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import (
    auc,
    average_precision_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedKFold
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS  --  must stay in sync with app.py
# ══════════════════════════════════════════════════════════════════════════════

FEATURE_ORDER = [
    "is_historical_payee",          # binary -- payee seen before?
    "is_vm_or_emulator",            # binary -- virtual machine detected?
    "webdriver_detected",           # binary -- browser automation?
    "is_known_location",            # binary -- familiar login geography?
    "profile_updated_this_session", # binary -- account details changed?
    "timezone_mismatch",            # binary -- device tz ≠ account tz?
    "pages_visited_pre_transfer",   # int    -- browsing depth before transfer
    "time_login_to_transfer_sec",   # float  -- seconds from login to send
    "used_fido_passkey",            # binary -- FIDO2/passkey used?
    "form_completion_time_sec",     # float  -- time to fill transfer form
    "password_entry_ms",            # float  -- ms spent entering password
    "mouse_linearity_score",        # float  -- 0=human curves, 1=bot lines
    "typing_cadence_score",         # float  -- 0=human rhythm, 1=bot uniform
    "is_neobank_routing",           # binary -- destination is neobank?
    "payee_account_age_hours",      # float  -- age of recipient account
    "is_in_active_call",            # binary -- phone call during session?
    "is_screensharing_active",      # binary -- screen-share detected?
    "remote_access_app_detected",   # binary -- AnyDesk / TeamViewer etc.?
    "trust_score_live",             # float  -- computed account trust [0-10]
    "session_tension",              # float  -- always 0 (kept for compat)
    "coached_fraud_index",          # float  -- coached-fraud behavioural signal
    "mule_potential",               # float  -- always 0 (kept for compat)
    "bot_agility_index",            # float  -- automated-interaction signal
    "transfer_intensity",           # float  -- amount / historical average
    "transfer_amount_lek",          # float  -- transfer value in ALL
    "transfers_past_24h",           # int    -- recent transfer frequency
    "ip_asn_type_encoded",          # int    -- 0=hosting 1=mobile 2=biz 3=res
]

# IP ASN encoding must match app.py's LabelEncoder fit order
IP_ASN_CLASSES = ["residential", "mobile", "business", "hosting"]

RANDOM_STATE = 42
MIN_RECALL   = 0.65     # recall floor when selecting optimal F1 threshold
OUTPUT_MODEL   = Path("fraud_engine.joblib")
OUTPUT_ENCODER = Path("label_encoder.joblib")


# ══════════════════════════════════════════════════════════════════════════════
#  SYNTHETIC DATASET GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_dataset(
    n_samples: int = 10_000,
    fraud_rate: float = 0.02,
    seed: int = RANDOM_STATE,
) -> tuple:
    """
    Generates a labelled synthetic dataset whose feature distributions mirror
    the Fibank 27-feature production vector.

    Class 0  =  legitimate transaction  (~98 % of samples)
    Class 1  =  fraudulent transaction  (  ~2 % of samples)

    Legitimate behaviour:
      Known payees, FIDO auth, residential IPs, organic mouse/typing,
      normal form-fill times, low transfer intensity.

    Fraudulent behaviour  (five attack archetypes blended):
      • APP / vishing  : social-engineering, active call, coached fraud
      • Phishing       : session-from-link, unknown location, short login time
      • Remote-access  : screensharing, RAT detected, VM
      • Bot / ATO      : high mouse linearity, webdriver, fast form-fill
      • Money mule     : new neobank payee, high transfer intensity
    """
    rng = np.random.default_rng(seed)

    n_fraud = max(int(n_samples * fraud_rate), 50)
    n_legit = n_samples - n_fraud

    def bern(p, n):
        return rng.binomial(1, p, n).astype(np.float32)

    def clip32(arr, lo, hi):
        return np.clip(arr, lo, hi).astype(np.float32)

    # -- Legitimate transactions -----------------------------------------------
    L = {}
    L["is_historical_payee"]          = bern(0.84, n_legit)
    L["is_vm_or_emulator"]            = bern(0.01, n_legit)
    L["webdriver_detected"]           = bern(0.005, n_legit)
    L["is_known_location"]            = bern(0.90, n_legit)
    L["profile_updated_this_session"] = bern(0.05, n_legit)
    L["timezone_mismatch"]            = bern(0.07, n_legit)
    L["pages_visited_pre_transfer"]   = clip32(rng.poisson(4.0, n_legit), 1, 12)
    L["time_login_to_transfer_sec"]   = clip32(rng.lognormal(4.5, 0.7, n_legit), 30, 1800)
    L["used_fido_passkey"]            = bern(0.76, n_legit)
    L["form_completion_time_sec"]     = clip32(rng.normal(32, 14, n_legit), 5, 180)
    L["password_entry_ms"]            = clip32(rng.normal(2600, 900, n_legit), 400, 9000)
    L["mouse_linearity_score"]        = clip32(rng.beta(2, 8, n_legit), 0.0, 1.0)
    L["typing_cadence_score"]         = clip32(rng.beta(2, 8, n_legit), 0.0, 1.0)
    L["is_neobank_routing"]           = bern(0.12, n_legit)
    # Historical payees: high age; new payees: random 12-720 h
    L["payee_account_age_hours"]      = np.where(
        L["is_historical_payee"],
        clip32(rng.normal(7000, 1000, n_legit), 2000, 8760),
        clip32(rng.uniform(12, 720, n_legit), 12, 720),
    ).astype(np.float32)
    L["is_in_active_call"]            = bern(0.02, n_legit)
    L["is_screensharing_active"]      = bern(0.02, n_legit)
    L["remote_access_app_detected"]   = bern(0.01, n_legit)
    L["trust_score_live"]             = clip32(rng.normal(6.2, 1.4, n_legit), 0.0, 10.0)
    L["session_tension"]              = np.zeros(n_legit, dtype=np.float32)
    L["coached_fraud_index"]          = bern(0.01, n_legit)
    L["mule_potential"]               = np.zeros(n_legit, dtype=np.float32)
    L["bot_agility_index"]            = bern(0.01, n_legit)
    L["transfer_intensity"]           = clip32(rng.lognormal(0.3, 0.5, n_legit), 0.05, 6.0)
    L["transfer_amount_lek"]          = clip32(rng.lognormal(10.0, 1.1, n_legit), 1_000, 800_000)
    L["transfers_past_24h"]           = clip32(rng.poisson(1.2, n_legit), 0, 8)
    # 70 % residential, 20 % mobile, 8 % business, 2 % hosting
    L["ip_asn_type_encoded"]          = rng.choice(
        [3, 1, 2, 0], p=[0.70, 0.20, 0.08, 0.02], size=n_legit,
    ).astype(np.float32)

    # -- Fraudulent transactions -----------------------------------------------
    F = {}
    F["is_historical_payee"]          = bern(0.14, n_fraud)
    F["is_vm_or_emulator"]            = bern(0.38, n_fraud)
    F["webdriver_detected"]           = bern(0.22, n_fraud)
    F["is_known_location"]            = bern(0.28, n_fraud)
    F["profile_updated_this_session"] = bern(0.58, n_fraud)
    F["timezone_mismatch"]            = bern(0.62, n_fraud)
    F["pages_visited_pre_transfer"]   = clip32(rng.poisson(1.4, n_fraud), 1, 4)

    # Fraudsters either RUSH (automated) or take VERY LONG (being coached)
    rush_mask = rng.binomial(1, 0.60, n_fraud).astype(bool)
    F["time_login_to_transfer_sec"]   = np.where(
        rush_mask,
        clip32(rng.normal(18, 10, n_fraud), 5, 60),
        clip32(rng.normal(900, 300, n_fraud), 300, 2400),
    ).astype(np.float32)

    F["used_fido_passkey"]            = bern(0.13, n_fraud)

    # Very fast form-fill = automated script; very slow = being coached on phone
    auto_mask = rng.binomial(1, 0.55, n_fraud).astype(bool)
    F["form_completion_time_sec"]     = np.where(
        auto_mask,
        clip32(rng.normal(3.0, 1.5, n_fraud), 1, 8),
        clip32(rng.normal(90, 30, n_fraud), 40, 200),
    ).astype(np.float32)

    # Pasted password (very fast) OR coached hesitation (very slow)
    paste_mask = rng.binomial(1, 0.50, n_fraud).astype(bool)
    F["password_entry_ms"]            = np.where(
        paste_mask,
        clip32(rng.normal(280, 90, n_fraud), 80, 600),
        clip32(rng.normal(8500, 2000, n_fraud), 4000, 15000),
    ).astype(np.float32)

    F["mouse_linearity_score"]        = clip32(rng.beta(7, 2, n_fraud), 0.0, 1.0)
    F["typing_cadence_score"]         = clip32(rng.beta(7, 2, n_fraud), 0.0, 1.0)
    F["is_neobank_routing"]           = bern(0.57, n_fraud)
    F["payee_account_age_hours"]      = clip32(rng.uniform(1, 72, n_fraud), 1, 72)
    F["is_in_active_call"]            = bern(0.48, n_fraud)
    F["is_screensharing_active"]      = bern(0.52, n_fraud)
    F["remote_access_app_detected"]   = bern(0.62, n_fraud)
    F["trust_score_live"]             = clip32(rng.normal(1.8, 1.0, n_fraud), 0.0, 5.0)
    F["session_tension"]              = np.zeros(n_fraud, dtype=np.float32)
    F["coached_fraud_index"]          = bern(0.63, n_fraud)
    F["mule_potential"]               = np.zeros(n_fraud, dtype=np.float32)
    F["bot_agility_index"]            = bern(0.58, n_fraud)
    F["transfer_intensity"]           = clip32(rng.lognormal(2.2, 0.8, n_fraud), 3.0, 25.0)
    F["transfer_amount_lek"]          = clip32(rng.lognormal(11.5, 0.9, n_fraud), 50_000, 2_000_000)
    F["transfers_past_24h"]           = clip32(rng.poisson(4.5, n_fraud), 0, 15)
    # Fraudsters favour hosting / business IPs
    F["ip_asn_type_encoded"]          = rng.choice(
        [3, 1, 2, 0], p=[0.12, 0.18, 0.28, 0.42], size=n_fraud,
    ).astype(np.float32)

    # -- Assemble & shuffle ----------------------------------------------------
    X_legit = np.column_stack([L[f] for f in FEATURE_ORDER])
    X_fraud = np.column_stack([F[f] for f in FEATURE_ORDER])
    X = np.vstack([X_legit, X_fraud])
    y = np.concatenate([np.zeros(n_legit, dtype=np.int32),
                        np.ones(n_fraud,  dtype=np.int32)])
    perm = rng.permutation(len(y))
    return X[perm], y[perm]


# ══════════════════════════════════════════════════════════════════════════════
#  THRESHOLD SELECTION  --  recall-constrained optimal F1
# ══════════════════════════════════════════════════════════════════════════════

def find_best_threshold(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    min_recall: float = MIN_RECALL,
) -> float:
    """
    Selects the decision threshold that maximises F1-Score subject to
    recall >= min_recall.

    Why a recall floor?
    Without it, a model can inflate precision by raising the threshold until
    it only flags obvious fraud -- but that means missing subtler cases.
    The floor enforces: "we must catch at least MIN_RECALL of all fraud,
    no matter what."

    Falls back to the unconstrained best-F1 threshold if no threshold can
    satisfy the recall floor (e.g. extremely imbalanced validation folds).
    """
    precision_arr, recall_arr, thresholds = precision_recall_curve(y_true, y_prob)

    # Remove the trailing sentinel point added by sklearn (no matching threshold)
    p = precision_arr[:-1]
    r = recall_arr[:-1]

    denom   = p + r
    f1_arr  = np.where(denom > 0, 2 * p * r / denom, 0.0)

    constrained = r >= min_recall
    if constrained.any():
        best_idx = int(np.argmax(f1_arr * constrained))
    else:
        # Recall floor unreachable -- fall back to unconstrained optimum
        best_idx = int(np.argmax(f1_arr))

    return float(thresholds[best_idx])


# ══════════════════════════════════════════════════════════════════════════════
#  COMPOSITE METRIC
# ══════════════════════════════════════════════════════════════════════════════

def composite_score(pr_auc: float, f1: float, alpha: float) -> float:
    """
    Weighted combination of PR-AUC and F1.  Higher is better.

    alpha = 0.60 means PR-AUC contributes 60 % and F1 contributes 40 %.
    PR-AUC is weighted more heavily because it captures model behaviour
    across ALL thresholds, while F1 only measures quality at ONE threshold.
    """
    return alpha * pr_auc + (1.0 - alpha) * f1


# ══════════════════════════════════════════════════════════════════════════════
#  MODEL FACTORY
# ══════════════════════════════════════════════════════════════════════════════

def make_model(scale_pos_weight: float, n_estimators: int = 400) -> XGBClassifier:
    """
    Returns an XGBClassifier tuned for severe class imbalance.

    scale_pos_weight
        The single most important imbalance parameter.  XGBoost multiplies
        the gradient of every positive (fraud) sample by this value, so
        misclassifying one fraud example costs as much as misclassifying
        scale_pos_weight legitimate examples.

    max_depth = 4
        Shallow trees.  Deep trees memorise rare fraud patterns in the
        training set rather than learning generalisable rules.

    min_child_weight = 6
        A leaf must cover at least 6 samples.  With a 2 % fraud rate and
        scale_pos_weight=49 this effectively requires covering ~6 fraud
        examples, preventing leaves that overfit to individual outliers.

    gamma = 1.0
        Minimum loss-reduction required to make a split.  Acts as a
        complexity penalty: the model must actually improve PR-AUC to
        justify adding another branch.

    eval_metric = 'aucpr'
        XGBoost's native PR-AUC metric for internal progress monitoring
        (distinct from our outer CV metric, but correlated).

    subsample / colsample_bytree = 0.80
        Stochastic gradient boosting: each tree sees 80 % of rows and
        80 % of features, reducing variance and co-adaptation between trees.
    """
    return XGBClassifier(
        objective          = "binary:logistic",
        eval_metric        = "aucpr",
        n_estimators       = n_estimators,
        learning_rate      = 0.05,
        max_depth          = 4,
        min_child_weight   = 6,
        gamma              = 1.0,
        subsample          = 0.80,
        colsample_bytree   = 0.80,
        reg_alpha          = 0.10,
        reg_lambda         = 1.50,
        scale_pos_weight   = scale_pos_weight,  # <- set per fold dynamically
        tree_method        = "hist",
        random_state       = RANDOM_STATE,
        n_jobs             = -1,
        verbosity          = 0,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  STRATIFIED K-FOLD CROSS-VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def run_stratified_cv(
    X: np.ndarray,
    y: np.ndarray,
    n_folds: int = 5,
    alpha: float = 0.60,
) -> dict:
    """
    Runs Stratified K-Fold CV with per-fold dynamic scale_pos_weight.

    Why stratify?
    -------------
    With 2 % fraud, a naive random split on 10,000 samples gives ~200 fraud
    cases.  Over 5 folds each validation set has ~40 fraud cases on average,
    but random variation could produce folds with 20 or 60.  Stratification
    fixes exactly 40 per fold, ensuring every validation set is a fair
    representative sample and the metric variance reflects model variance --
    not sampling luck.

    Why compute scale_pos_weight per fold?
    ---------------------------------------
    Computing it from the full dataset and applying it to all folds leaks
    the minority-class frequency of the held-out validation data into the
    model.  Per-fold computation uses only the training portion, which is
    what the model actually sees during training.
    """
    skf = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=RANDOM_STATE)

    records = []
    best_composite = -1.0
    best_model     = None

    sep = "-" * 88
    hdr = (
        f"  {'Fold':>4}  {'n_fraud_tr':>10}  {'n_fraud_val':>11}  {'SPW':>6}  "
        f"{'PR-AUC':>7}  {'ROC-AUC':>8}  {'F1':>6}  "
        f"{'Prec':>6}  {'Recall':>7}  {'Thresh':>7}  {'Composite':>10}"
    )
    print(f"\n{sep}")
    print(f"  Stratified {n_folds}-Fold Cross-Validation "
          f"(recall floor = {MIN_RECALL:.0%})")
    print(sep)
    print(hdr)
    print(sep)

    for fold_idx, (train_idx, val_idx) in enumerate(skf.split(X, y), start=1):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

        # -- 1. Dynamic scale_pos_weight from THIS fold's training set only ----
        n_neg = int((y_tr == 0).sum())
        n_pos = int((y_tr == 1).sum())
        spw   = round(n_neg / max(n_pos, 1), 2)

        # -- 2. Train ----------------------------------------------------------
        model = make_model(scale_pos_weight=spw)
        model.fit(
            X_tr, y_tr,
            eval_set = [(X_val, y_val)],
            verbose  = False,
        )

        # -- 3. Score ----------------------------------------------------------
        y_prob  = model.predict_proba(X_val)[:, 1]
        pr_auc  = average_precision_score(y_val, y_prob)
        roc_auc = roc_auc_score(y_val, y_prob)

        # -- 4. Recall-constrained optimal F1 threshold ------------------------
        thresh = find_best_threshold(y_val, y_prob, min_recall=MIN_RECALL)
        y_pred = (y_prob >= thresh).astype(int)

        fold_f1   = f1_score(y_val, y_pred, zero_division=0)
        fold_prec = precision_score(y_val, y_pred, zero_division=0)
        fold_rec  = recall_score(y_val, y_pred, zero_division=0)
        comp      = composite_score(pr_auc, fold_f1, alpha)

        records.append(dict(
            fold=fold_idx, pr_auc=pr_auc, roc_auc=roc_auc,
            f1=fold_f1, precision=fold_prec, recall=fold_rec,
            threshold=thresh, composite=comp, spw=spw,
            n_train_fraud=n_pos, n_val_fraud=int(y_val.sum()),
        ))

        print(
            f"  {fold_idx:>4}  {n_pos:>10,}  {int(y_val.sum()):>11,}  {spw:>6.1f}  "
            f"{pr_auc:>7.4f}  {roc_auc:>8.4f}  {fold_f1:>6.4f}  "
            f"{fold_prec:>6.4f}  {fold_rec:>7.4f}  {thresh:>7.4f}  {comp:>10.4f}"
        )

        if comp > best_composite:
            best_composite = comp
            best_model = model

    # -- Summary row -----------------------------------------------------------
    print(sep)
    metric_keys = ["pr_auc", "roc_auc", "f1", "precision", "recall", "composite"]
    means = {k: float(np.mean([r[k] for r in records])) for k in metric_keys}
    stds  = {k: float(np.std( [r[k] for r in records])) for k in metric_keys}

    print(
        f"  {'MEAN':>4}  {'':>10}  {'':>11}  {np.mean([r['spw'] for r in records]):>6.1f}  "
        f"{means['pr_auc']:>7.4f}  {means['roc_auc']:>8.4f}  {means['f1']:>6.4f}  "
        f"{means['precision']:>6.4f}  {means['recall']:>7.4f}  {'--':>7}  {means['composite']:>10.4f}"
    )
    print(
        f"  {'+/-STD':>4}  {'':>10}  {'':>11}  {'':>6}  "
        f"+/-{stds['pr_auc']:>6.4f}  +/-{stds['roc_auc']:>7.4f}  +/-{stds['f1']:>5.4f}  "
        f"+/-{stds['precision']:>5.4f}  +/-{stds['recall']:>6.4f}  {'':>7}  "
        f"+/-{stds['composite']:>9.4f}"
    )
    print(sep)

    return dict(
        records=records,
        means=means,
        stds=stds,
        best_model=best_model,
        best_composite=best_composite,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  FINAL MODEL  --  retrain on full dataset
# ══════════════════════════════════════════════════════════════════════════════

def train_final_model(X: np.ndarray, y: np.ndarray) -> XGBClassifier:
    """
    Retrains on the FULL dataset using the dynamically computed
    scale_pos_weight.  This is the model serialised to fraud_engine.joblib.

    We retrain from scratch rather than using the best CV fold's model
    because the CV models were each trained on only (1-1/k) of the data.
    The final model has access to all k*(1-1/k) unique samples.
    """
    n_neg = int((y == 0).sum())
    n_pos = int((y == 1).sum())
    spw   = round(n_neg / max(n_pos, 1), 2)

    print(f"\n  n_total={len(y):,}  fraud={n_pos:,} ({n_pos/len(y)*100:.2f}%)")
    print(f"  scale_pos_weight = {n_neg:,} / {n_pos:,} = {spw:.2f}")

    model = make_model(scale_pos_weight=spw)
    model.fit(X, y, verbose=False)
    return model


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE IMPORTANCE
# ══════════════════════════════════════════════════════════════════════════════

def print_feature_importance(model: XGBClassifier, top_n: int = 12) -> None:
    importances = model.feature_importances_
    ranked = sorted(
        zip(FEATURE_ORDER, importances), key=lambda x: x[1], reverse=True
    )
    print(f"\n  Top {top_n} Feature Importances (XGBoost 'weight' gain):")
    print("  " + "-" * 56)
    for i, (feat, imp) in enumerate(ranked[:top_n], 1):
        bar = "#" * max(1, int(imp * 300))
        print(f"  {i:>2}. {feat:<40} {imp:.4f}  {bar}")


# ══════════════════════════════════════════════════════════════════════════════
#  ENCODER
# ══════════════════════════════════════════════════════════════════════════════

def build_encoder() -> dict:
    """
    LabelEncoder for ip_asn_type -- must match the classes and fit order used
    in app.py so that IP_ASN_ENCODER.transform(['residential']) returns the
    correct integer at inference time.
    """
    le = LabelEncoder()
    le.fit(IP_ASN_CLASSES)
    return {"ip_asn_type": le}


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fibank XGBoost Fraud Detection -- training pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--folds",       type=int,   default=5,
                   help="Stratified CV folds (default: 5)")
    p.add_argument("--samples",     type=int,   default=10_000,
                   help="Synthetic dataset size (default: 10,000)")
    p.add_argument("--fraud-rate",  type=float, default=0.02,
                   help="Fraud class ratio 0-1 (default: 0.02 = 2%%)")
    p.add_argument("--alpha",       type=float, default=0.60,
                   help="PR-AUC weight in composite score (default: 0.60)")
    p.add_argument("--no-retrain",  action="store_true",
                   help="CV only -- do not overwrite .joblib files")
    return p.parse_args()


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    args = parse_args()
    t0   = time.perf_counter()

    n_fraud_expected = max(int(args.samples * args.fraud_rate), 50)
    n_legit_expected = args.samples - n_fraud_expected
    ratio_expected   = n_legit_expected / n_fraud_expected

    sep = "=" * 88
    print(sep)
    print("  FIBANK FRAUD DETECTION  --  XGBoost Training Pipeline")
    print(sep)
    print(f"  Dataset    : {args.samples:,} samples  |  fraud rate: "
          f"{args.fraud_rate * 100:.1f}%  ({n_fraud_expected:,} fraud / "
          f"{n_legit_expected:,} legit)")
    print(f"  Features   : {len(FEATURE_ORDER)}")
    print(f"  CV folds   : {args.folds}  |  recall floor: {MIN_RECALL:.0%}")
    print(f"  Metric     : {args.alpha:.0%}*PR-AUC + {1-args.alpha:.0%}*F1")
    print(f"  Expected SPW : ~{ratio_expected:.0f}  "
          f"(legitimates per fraud in training data)")
    print(sep)

    # -- Step 1: Generate dataset -----------------------------------------------
    print("\n[1/4]  Generating synthetic dataset...")
    X, y = generate_dataset(
        n_samples=args.samples,
        fraud_rate=args.fraud_rate,
    )
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    print(f"       Legitimate : {n_neg:,}   Fraud : {n_pos:,}   "
          f"Ratio : {n_neg/n_pos:.0f}:1")

    # -- Step 2: Stratified K-Fold cross-validation ----------------------------
    print(f"\n[2/4]  Running Stratified {args.folds}-Fold CV...")
    cv = run_stratified_cv(X, y, n_folds=args.folds, alpha=args.alpha)
    means = cv["means"]
    stds  = cv["stds"]

    print("\n  Cross-Validation Summary:")
    print(f"    PR-AUC        {means['pr_auc']:.4f}  +/-{stds['pr_auc']:.4f}")
    print(f"    ROC-AUC       {means['roc_auc']:.4f}  +/-{stds['roc_auc']:.4f}")
    print(f"    F1-Score      {means['f1']:.4f}  +/-{stds['f1']:.4f}")
    print(f"    Precision     {means['precision']:.4f}  +/-{stds['precision']:.4f}")
    print(f"    Recall        {means['recall']:.4f}  +/-{stds['recall']:.4f}")
    print(f"    Composite     {means['composite']:.4f}  +/-{stds['composite']:.4f}")

    if means["recall"] < MIN_RECALL:
        print(
            f"\n  WARNING  WARNING: Mean recall ({means['recall']:.3f}) is below the "
            f"floor of {MIN_RECALL:.2f}.\n"
            f"     The model is trading recall for precision beyond acceptable limits.\n"
            f"     Suggestions: increase --fraud-rate, reduce min_child_weight,\n"
            f"     or lower MIN_RECALL if the business accepts more missed fraud."
        )

    if args.no_retrain:
        print(f"\n  --no-retrain flag set; skipping final model save.")
        print(f"\n  Done in {time.perf_counter() - t0:.1f}s\n")
        return

    # -- Step 3: Train final model on full dataset ------------------------------
    print("\n[3/4]  Training final model on full dataset...")
    final_model = train_final_model(X, y)

    # Full-dataset sanity check (optimistic -- model has seen this data)
    y_prob_all = final_model.predict_proba(X)[:, 1]
    thresh_all = find_best_threshold(y, y_prob_all, min_recall=MIN_RECALL)
    y_pred_all = (y_prob_all >= thresh_all).astype(int)

    print(f"\n  Full-dataset check (threshold = {thresh_all:.4f}, "
          f"optimistic -- training data):")
    report = classification_report(
        y, y_pred_all,
        target_names=["Legitimate", "Fraud"],
        digits=4,
    )
    for line in report.splitlines():
        print("    " + line)

    tn, fp, fn, tp = confusion_matrix(y, y_pred_all).ravel()
    print(f"\n    Confusion matrix:")
    print(f"      True  Negatives (legit  -> legit ) : {tn:>6,}")
    print(f"      False Positives (legit  -> fraud ) : {fp:>6,}  "
          f"<- {fp/(fp+tn)*100:.2f}% of legitimate flagged")
    print(f"      False Negatives (fraud  -> legit ) : {fn:>6,}  "
          f"<- {fn/(fn+tp)*100:.2f}% of fraud missed")
    print(f"      True  Positives (fraud  -> fraud ) : {tp:>6,}")

    print_feature_importance(final_model, top_n=12)

    # -- Step 4: Serialise ------------------------------------------------------
    print("\n[4/4]  Saving model artifacts...")
    encoders = build_encoder()
    joblib.dump(final_model, OUTPUT_MODEL,   compress=3)
    joblib.dump(encoders,    OUTPUT_ENCODER, compress=3)
    print(f"       OK {OUTPUT_MODEL}    "
          f"({OUTPUT_MODEL.stat().st_size / 1024:.1f} KB)")
    print(f"       OK {OUTPUT_ENCODER}  "
          f"({OUTPUT_ENCODER.stat().st_size / 1024:.1f} KB)")

    elapsed = time.perf_counter() - t0
    print(f"\n{sep}")
    print(f"  Training complete in {elapsed:.1f}s")
    print(f"  Best CV composite  : {cv['best_composite']:.4f}")
    print(f"  Final model saved  : {OUTPUT_MODEL.name}")
    print(sep + "\n")


if __name__ == "__main__":
    main()
