"""
IntentPay AI - Transaction Service
==================================
HTTP bağımsız transaction orchestration katmanı.

Bu dosya transaction değerlendirme akışını server.py dışına taşır:
- policy evaluation
- risk scoring
- final decision
- token issuing
- audit logging
- persistence write

Endpoint path ve response formatları korunur.
"""
from __future__ import annotations

from dataclasses import asdict

from models.schema import TransactionRequest, now_ms
from services.policy_engine import evaluate_policy
from services.risk_model import feature_vector, score_transaction
from services.decision_engine import decide
from services import persistence

from services.app_state import STATE


def evaluate_transaction(tx_dict: dict) -> dict:
    tx = TransactionRequest(**tx_dict)

    STATE.transactions[tx.transaction_id] = tx
    persistence.save_transaction(
        tx.transaction_id,
        tx.user_id,
        tx.__dict__,
        tx.status,
        now_ms(),
    )

    user = STATE.users[tx.user_id]
    agent = STATE.agents.get(tx.agent_id)
    merchant = STATE.merchants.get(tx.merchant_id)
    mandate = STATE.active_mandate_for(tx.user_id)

    STATE.audit.record(tx.transaction_id, "transaction_request", {
        "agent_id": tx.agent_id,
        "merchant": merchant.merchant_name if merchant else None,
        "amount": tx.amount,
        "currency": tx.currency,
        "category": tx.category,
        "cart": tx.cart_description,
        "note": tx.note,
    })

    if mandate is None:
        result = {"error": "Aktif mandate yok. Önce talimat girip onaylayın."}
        STATE.audit.record(tx.transaction_id, "decision", {
            "final_decision": "decline",
            "explanation": result["error"],
        })
        return result

    token_already_used = bool(
        tx.reused_token_id and STATE.tokens.is_used(tx.reused_token_id)
    )

    now = now_ms()
    velocity_count = sum(
        1 for t in STATE.tx_timestamps
        if t >= now - 60_000
    )
    STATE.tx_timestamps.append(now)

    policy = evaluate_policy(
        tx,
        mandate,
        merchant,
        agent,
        STATE.tokens.used_ids(),
        recent_tx_timestamps=STATE.tx_timestamps[:-1],
    )

    STATE.audit.record(tx.transaction_id, "policy_evaluation", {
        "preliminary_decision": policy.preliminary_decision,
        "passed": policy.passed_rules,
        "failed": policy.failed_rules,
        "warnings": policy.warnings,
    })

    feats = feature_vector(
        tx,
        mandate,
        merchant,
        agent,
        user,
        policy_failed_count=len(policy.failed_rules),
        token_already_used=token_already_used,
        velocity_count=velocity_count,
    )

    risk = score_transaction(feats, tx.transaction_id)

    STATE.audit.record(tx.transaction_id, "risk_scoring", {
        "risk_score": risk.risk_score,
        "risk_level": risk.risk_level,
        "suggested_action": risk.suggested_action,
        "top_risk_factors": risk.top_risk_factors,
        "features": feats,
        "mode": risk.__dict__.get("_mode"),
    })

    decision = decide(policy, risk, tx.transaction_id)

    token_dict = None

    if decision.final_decision == "approve":
        token = STATE.tokens.issue(tx, mandate, merchant)
        STATE.last_issued_token_id = token.token_id

        mandate.spent_so_far += tx.amount

        STATE.tokens.redeem(token.token_id)

        token_dict = asdict(token)
        decision.token = token_dict

        STATE.audit.record(tx.transaction_id, "token_issued", token_dict)

    elif decision.final_decision == "step-up":
        STATE.pending_stepups[tx.transaction_id] = {
            "amount": tx.amount,
        }

    STATE.audit.record(tx.transaction_id, "decision", {
        "final_decision": decision.final_decision,
        "explanation": decision.explanation,
        "explanation_factors": decision.explanation_factors,
    })

    tx.status = decision.final_decision

    return {
        "transaction": tx.__dict__,
        "policy_result": decision.policy_result,
        "risk_result": decision.risk_result,
        "final_decision": decision.final_decision,
        "explanation": decision.explanation,
        "explanation_factors": decision.explanation_factors,
        "token": token_dict,
        "velocity_count": velocity_count,
        "needs_stepup": decision.final_decision == "step-up",
        "mandate_spent": mandate.spent_so_far,
        "mandate_total_limit": mandate.total_limit,
    }