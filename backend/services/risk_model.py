"""
IntentPay AI - Risk Scoring Model
=================================
Her işlem için 0..1 risk skoru üretir + risk_level + suggested_action + en etkili
risk faktörleri (açıklanabilirlik).

İki katman:
  1. XGBoost modeli (varsa, train_risk_model.py ile eğitilir)  -> mode="xgboost"
  2. Deterministik ağırlıklı skor (her zaman çalışır)           -> mode="heuristic"

Açıklanabilirlik:
  - XGBoost: model.feature_importances_ + işlemin özellik değerleri birleştirilir
  - Fallback: faktör başına katkı doğrudan hesaplanır
"""
from __future__ import annotations

import os

from models.schema import (
    Mandate, Merchant, Agent, User, TransactionRequest, RiskResult,
)
from data.synthetic import FEATURE_NAMES, FEATURE_LABELS

_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "risk_model.json")

# Deterministik fallback ağırlıkları (training latent risk ile hizalı)
_WEIGHTS = {
    "amount_ratio":      0.30,   # >1 olan kısım çarpılır
    "merchant_trust":    0.40,   # (1 - trust)
    "is_new_merchant":   0.40,
    "category_mismatch": 0.70,
    "attempt_count":     0.10,
    "odd_hour":          0.15,
    "young_agent":       0.25,
    "prior_failure":     0.30,
    "token_reuse":       0.90,
    "device_change":     0.20,
    "mandate_mismatch":  0.50,
}

_xgb_model = None
_xgb_importance: dict[str, float] | None = None


# --------------------------------------------------------------------------- #
#  Feature extraction
# --------------------------------------------------------------------------- #
def feature_vector(
    tx: TransactionRequest,
    mandate: Mandate,
    merchant: Merchant,
    agent: Agent,
    user: User,
    policy_failed_count: int,
    token_already_used: bool,
    velocity_count: int = 0,
) -> dict[str, float]:
    avg = user.average_transaction_amount or 1.0
    return {
        "amount_ratio":      round(tx.amount / avg, 3),
        "merchant_trust":    round(merchant.trust_score, 3),
        "is_new_merchant":   0.0 if merchant.is_approved else 1.0,
        "category_mismatch": 1.0 if (mandate.allowed_categories
                                     and tx.category not in mandate.allowed_categories) else 0.0,
        "attempt_count":     float(velocity_count),
        "odd_hour":          0.0,
        "young_agent":       1.0 if agent.age_days < 7 else 0.0,
        "prior_failure":     0.0,
        "token_reuse":       1.0 if token_already_used else 0.0,
        "device_change":     0.0,
        "mandate_mismatch":  float(policy_failed_count),
    }


# --------------------------------------------------------------------------- #
#  Model loading
# --------------------------------------------------------------------------- #
def _load_xgb():
    global _xgb_model, _xgb_importance
    if _xgb_model is not None:
        return _xgb_model
    if not os.path.exists(_MODEL_PATH):
        return None
    try:
        import xgboost as xgb
        m = xgb.XGBClassifier()
        m.load_model(_MODEL_PATH)
        _xgb_model = m
        imp = m.feature_importances_
        _xgb_importance = {FEATURE_NAMES[i]: float(imp[i]) for i in range(len(FEATURE_NAMES))}
        return m
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Scoring
# --------------------------------------------------------------------------- #
def score_transaction(features: dict[str, float], transaction_id: str) -> RiskResult:
    model = _load_xgb()
    if model is not None:
        score, mode = _score_xgb(model, features), "xgboost"
    else:
        score, mode = _score_heuristic(features), "heuristic"

    score = max(0.0, min(1.0, score))
    level = "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"

    if score >= 0.85:
        action = "decline"
    elif score >= 0.7:
        action = "review"
    elif score >= 0.4:
        action = "step-up"
    else:
        action = "approve"

    factors = _explain(features, mode)

    rr = RiskResult(
        transaction_id=transaction_id,
        risk_score=round(score, 3),
        risk_level=level,
        top_risk_factors=factors,
        suggested_action=action,
    )
    # mode'u dışarı taşımak için dict'e iliştirilecek (server tarafında)
    rr.__dict__["_mode"] = mode
    return rr


def _score_xgb(model, features: dict[str, float]) -> float:
    import numpy as np
    vec = np.array([[features[name] for name in FEATURE_NAMES]], dtype=float)
    return float(model.predict_proba(vec)[0][1])


def _score_heuristic(features: dict[str, float]) -> float:
    raw = 0.0
    raw += _WEIGHTS["amount_ratio"]      * max(0.0, features["amount_ratio"] - 1.0)
    raw += _WEIGHTS["merchant_trust"]    * (1.0 - features["merchant_trust"])
    raw += _WEIGHTS["is_new_merchant"]   * features["is_new_merchant"]
    raw += _WEIGHTS["category_mismatch"] * features["category_mismatch"]
    raw += _WEIGHTS["attempt_count"]     * min(features["attempt_count"], 3)
    raw += _WEIGHTS["odd_hour"]          * features["odd_hour"]
    raw += _WEIGHTS["young_agent"]       * features["young_agent"]
    raw += _WEIGHTS["prior_failure"]     * features["prior_failure"]
    raw += _WEIGHTS["token_reuse"]       * features["token_reuse"]
    raw += _WEIGHTS["device_change"]     * features["device_change"]
    raw += _WEIGHTS["mandate_mismatch"]  * features["mandate_mismatch"]
    # 0..1'e sıkıştır (logistic)
    return 1 / (1 + 2.71828 ** (-(raw - 1.1) * 2.2))


# --------------------------------------------------------------------------- #
#  Explainability
# --------------------------------------------------------------------------- #
def _explain(features: dict[str, float], mode: str) -> list[dict]:
    """Her faktörün skora katkısını hesaplar, en yüksek 4'ünü döner."""
    contribs: list[tuple[str, float, float]] = []

    if mode == "xgboost" and _xgb_importance:
        for name in FEATURE_NAMES:
            val = features[name]
            norm = val if name == "merchant_trust" else (val - 1 if name == "amount_ratio" else val)
            # merchant_trust düşükse risk -> ters çevir
            signal = (1 - val) if name == "merchant_trust" else max(0.0, norm)
            contribs.append((name, _xgb_importance[name] * signal, val))
    else:
        c = features
        contribs = [
            ("amount_ratio",      _WEIGHTS["amount_ratio"] * max(0.0, c["amount_ratio"] - 1.0), c["amount_ratio"]),
            ("merchant_trust",    _WEIGHTS["merchant_trust"] * (1 - c["merchant_trust"]), c["merchant_trust"]),
            ("is_new_merchant",   _WEIGHTS["is_new_merchant"] * c["is_new_merchant"], c["is_new_merchant"]),
            ("category_mismatch", _WEIGHTS["category_mismatch"] * c["category_mismatch"], c["category_mismatch"]),
            ("attempt_count",     _WEIGHTS["attempt_count"] * min(c["attempt_count"], 3), c["attempt_count"]),
            ("odd_hour",          _WEIGHTS["odd_hour"] * c["odd_hour"], c["odd_hour"]),
            ("young_agent",       _WEIGHTS["young_agent"] * c["young_agent"], c["young_agent"]),
            ("prior_failure",     _WEIGHTS["prior_failure"] * c["prior_failure"], c["prior_failure"]),
            ("token_reuse",       _WEIGHTS["token_reuse"] * c["token_reuse"], c["token_reuse"]),
            ("device_change",     _WEIGHTS["device_change"] * c["device_change"], c["device_change"]),
            ("mandate_mismatch",  _WEIGHTS["mandate_mismatch"] * c["mandate_mismatch"], c["mandate_mismatch"]),
        ]

    contribs.sort(key=lambda x: x[1], reverse=True)
    out = []
    for name, contrib, val in contribs[:4]:
        if contrib <= 0.001:
            continue
        out.append({
            "factor": FEATURE_LABELS[name],
            "factor_key": name,
            "contribution": round(contrib, 3),
            "value": val,
        })
    return out