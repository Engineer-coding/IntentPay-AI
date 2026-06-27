"""
IntentPay AI - Decision Engine + Explainability
===============================================
Policy Engine sonucu ile Risk skorunu birleştirip NİHAİ kararı üretir ve
insan-okunur açıklama oluşturur.

Karar mantığı (öncelik sırası):
  1. Policy kesin ihlal (decline)            -> DECLINE
  2. Risk skoru çok yüksek (>= 0.85)         -> DECLINE
  3. Policy temiz + risk düşük               -> APPROVE
  4. Policy uyarı (step-up) veya orta risk   -> STEP-UP
  5. Yüksek risk ama kesin ihlal yok         -> MANUAL REVIEW
"""
from __future__ import annotations

from models.schema import PolicyResult, RiskResult, Decision, now_ms, CATEGORIES


def decide(policy: PolicyResult, risk: RiskResult, transaction_id: str) -> Decision:
    factors: list[str] = []

    # --- 1. kesin policy ihlali ---
    if policy.failed_rules:
        final = "decline"
        reasons = [f["reason"] for f in policy.failed_rules]
        explanation = "İşlem reddedildi: " + " ".join(reasons)
        factors = reasons

    # --- 2. çok yüksek risk -> decline ---
    elif risk.risk_score >= 0.85:
        final = "decline"
        explanation = (f"İşlem reddedildi çünkü risk skoru çok yüksek "
                       f"({risk.risk_score:.2f}).")
        factors = [f["factor"] for f in risk.top_risk_factors]

    # --- 3. policy temiz + risk düşük -> approve ---
    elif not policy.warnings and risk.risk_score < 0.4:
        final = "approve"
        explanation = ("İşlem onaylandı: tutar limit içinde, kategori uygun, "
                       "satıcı güvenilir ve risk düşük.")
        factors = ["Tüm policy kuralları geçti", f"Düşük risk skoru ({risk.risk_score:.2f})"]

    # --- 4. policy uyarısı veya orta risk -> step-up ---
    elif policy.warnings or risk.risk_score < 0.7:
        final = "step-up"
        warn_reasons = [w["reason"] for w in policy.warnings]
        risk_note = (f"İşlem riski orta seviyede ({risk.risk_score:.2f})."
                     if risk.risk_score >= 0.4 else "")
        explanation = "Ek kullanıcı onayı gerekli. " + " ".join(warn_reasons) + " " + risk_note
        factors = warn_reasons + [f["factor"] for f in risk.top_risk_factors[:2]]

    # --- 5. yüksek risk, kesin ihlal yok -> review ---
    else:
        final = "review"
        explanation = (f"İşlem manuel incelemeye yönlendirildi: kesin kural ihlali yok "
                       f"ancak risk yüksek ({risk.risk_score:.2f}).")
        factors = [f["factor"] for f in risk.top_risk_factors]

    return Decision(
        transaction_id=transaction_id,
        final_decision=final,
        explanation=explanation.strip(),
        explanation_factors=[f for f in factors if f],
        policy_result=_policy_to_dict(policy),
        risk_result=_risk_to_dict(risk),
        token=None,
        timestamp=now_ms(),
    )


def _policy_to_dict(p: PolicyResult) -> dict:
    return {
        "passed_rules": p.passed_rules,
        "failed_rules": p.failed_rules,
        "warnings": p.warnings,
        "preliminary_decision": p.preliminary_decision,
    }


def _risk_to_dict(r: RiskResult) -> dict:
    return {
        "risk_score": r.risk_score,
        "risk_level": r.risk_level,
        "suggested_action": r.suggested_action,
        "top_risk_factors": r.top_risk_factors,
        "mode": r.__dict__.get("_mode", "heuristic"),
    }