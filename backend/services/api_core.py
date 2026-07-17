"""
IntentPay AI - API Core
=======================
FastAPI route'ları için HTTP bağımsız orchestration fonksiyonları.

Bu dosya Phase 2 FastAPI migration içindir:
- HTTP Handler bağımlılığı kaldırılır.
- FastAPI route'ları doğrudan bu fonksiyonları çağırır.
- Core parser / policy / risk / decision / persistence logic değiştirilmez.
"""
from __future__ import annotations

from dataclasses import asdict

from services.transaction_service import evaluate_transaction as evaluate_transaction_core

from models.schema import Mandate, now_ms
from services.intent_parser import parse_intent as parse_natural_intent
from services.agent_simulator import build_request, list_scenarios
from services.attack_detector import scan_text, sanitize_mandate
from services import persistence

from services.app_state import STATE, DEFAULT_USER


RULE_TR = {
    "mandate_status": "Mandate durumu",
    "validity_date": "Geçerlilik süresi",
    "agent_authorization": "Ajan yetkisi",
    "token_reuse": "Token tekrar kullanımı",
    "blocked_category": "Yasaklı kategori",
    "allowed_category": "İzinli kategori",
    "amount_limit": "Tutar limiti",
    "total_spending_limit": "Toplam harcama tavanı",
    "merchant_approval": "Satıcı onayı",
    "new_merchant_stepup": "Yeni satıcı kontrolü",
    "velocity_limit": "Hız limiti (velocity)",
    "mcc_category_consistency": "MCC kategori tutarlılığı",
    "mcc_category_mismatch": "MCC kategori uyumsuzluğu",
    "mcc_blocked_category": "MCC yasaklı kategori",
    "mcc_unknown": "Bilinmeyen MCC kodu",
}


def bootstrap() -> dict:
    return {
        "users": {k: asdict(v) for k, v in STATE.users.items()},
        "agents": {
            k: {**asdict(v), "age_days": round(v.age_days, 1)}
            for k, v in STATE.agents.items()
        },
        "merchants": {k: asdict(v) for k, v in STATE.merchants.items()},
        "products": STATE.products,
        "scenarios": list_scenarios(),
        "default_user": DEFAULT_USER,
        "categories": __import__("models.schema", fromlist=["CATEGORIES"]).CATEGORIES,
    }


def audit() -> dict:
    return {
        "transactions": STATE.audit.grouped_by_transaction(),
        "raw": STATE.audit.all(),
    }


def tokens() -> dict:
    return {"tokens": STATE.tokens.all()}


def persistence_stats() -> dict:
    return persistence.stats()


def parse_intent(body: dict) -> dict:
    text = (body.get("text") or "").strip()
    user_id = body.get("user_id", DEFAULT_USER)
    company_id = body.get("company_id")

    if not text:
        return {"error": "Talimat metni boş olamaz."}
    if len(text) > 2000:
        return {"error": "Talimat çok uzun (en fazla 2000 karakter)."}
    if user_id not in STATE.users:
        return {"error": f"Bilinmeyen kullanıcı: {user_id}"}

    scan = scan_text(text)

    if scan["is_attack"]:
        STATE.audit.record("security:" + str(now_ms()), "intent_blocked", {
            "original_intent": text,
            "security_scan": scan,
            "reason": "Prompt injection/manipülasyon tespit edildiği için mandate oluşturulmadı.",
        })
        return {
            "blocked": True,
            "parse_mode": "blocked",
            "security_scan": scan,
            "mandate": None,
            "error": "Manipülasyon denemesi tespit edildi. Talimat reddedildi; mandate oluşturulmadı.",
        }

    result = parse_natural_intent(text, user_id, company_id=company_id)
    result["mandate"] = sanitize_mandate(result["mandate"], scan)
    result["security_scan"] = scan

    mandate = Mandate(**{
        k: v for k, v in result["mandate"].items()
        if not k.startswith("_")
    })

    STATE.mandates[mandate.mandate_id] = mandate
    persistence.save_mandate(
        mandate.mandate_id,
        mandate.user_id,
        mandate.to_dict(),
        mandate.status,
        now_ms(),
    )

    STATE.audit.record("mandate:" + mandate.mandate_id, "intent_parsed", {
        "original_intent": text,
        "parse_mode": result["parse_mode"],
        "security_scan": scan,
        "mandate": result["mandate"],
    })

    STATE.audit.record("mandate:" + mandate.mandate_id, "mandate_created", {
        "created_at": now_ms(),
        "mandate_id": mandate.mandate_id,
        "user_id": mandate.user_id,
        "summary": {
            "max_amount": mandate.max_amount,
            "total_limit": mandate.total_limit,
            "allowed_categories": mandate.allowed_categories,
            "blocked_categories": mandate.blocked_categories,
            "approved_only": "__approved_only__" in mandate.allowed_merchants,
            "requires_approval_for_new_merchant": mandate.requires_approval_for_new_merchant,
            "risk_threshold": mandate.risk_threshold,
            "status": mandate.status,
        },
        "reason": "Doğal dil talimatı yapılandırılmış mandate kurallarına çevrildi.",
    })

    return result


def update_mandate(body: dict) -> dict:
    mandate_id = body.get("mandate_id")
    updates = body.get("updates") or {}

    mandate = STATE.mandates.get(mandate_id)

    if not mandate:
        return {"error": "Mandate bulunamadı."}

    if mandate.status != "pending":
        return {"error": "Sadece pending durumdaki mandate düzenlenebilir."}

    allowed_fields = {
        "max_amount",
        "total_limit",
        "allowed_categories",
        "blocked_categories",
        "allowed_merchants",
        "requires_approval_for_new_merchant",
        "valid_from",
        "valid_until",
        "risk_threshold",
        "single_use_tokens",
    }

    unknown = sorted(set(updates.keys()) - allowed_fields)
    if unknown:
        return {"error": f"Geçersiz mandate alanı: {', '.join(unknown)}"}

    categories = __import__("models.schema", fromlist=["CATEGORIES"]).CATEGORIES

    if "max_amount" in updates:
        value = float(updates["max_amount"])
        if value <= 0:
            return {"error": "Maksimum işlem tutarı 0'dan büyük olmalıdır."}
        mandate.max_amount = value

    if "total_limit" in updates:
        value = float(updates["total_limit"])
        if value <= 0:
            return {"error": "Toplam limit 0'dan büyük olmalıdır."}
        mandate.total_limit = value

    if mandate.total_limit < mandate.max_amount:
        return {"error": "Toplam limit, maksimum işlem tutarından küçük olamaz."}

    if "allowed_categories" in updates:
        vals = list(updates["allowed_categories"] or [])
        bad = [x for x in vals if x not in categories]
        if bad:
            return {"error": f"Bilinmeyen izinli kategori: {', '.join(bad)}"}
        mandate.allowed_categories = vals

    if "blocked_categories" in updates:
        vals = list(updates["blocked_categories"] or [])
        bad = [x for x in vals if x not in categories]
        if bad:
            return {"error": f"Bilinmeyen yasaklı kategori: {', '.join(bad)}"}
        mandate.blocked_categories = vals

    overlap = sorted(set(mandate.allowed_categories) & set(mandate.blocked_categories))
    if overlap:
        return {"error": f"Kategori aynı anda izinli ve yasaklı olamaz: {', '.join(overlap)}"}

    if "allowed_merchants" in updates:
        vals = list(updates["allowed_merchants"] or [])
        mandate.allowed_merchants = vals

    if "requires_approval_for_new_merchant" in updates:
        mandate.requires_approval_for_new_merchant = bool(
            updates["requires_approval_for_new_merchant"]
        )

    if "valid_from" in updates:
        mandate.valid_from = int(updates["valid_from"])

    if "valid_until" in updates:
        mandate.valid_until = int(updates["valid_until"])

    if mandate.valid_until <= mandate.valid_from:
        return {"error": "Geçerlilik bitiş tarihi başlangıçtan sonra olmalıdır."}

    if "risk_threshold" in updates:
        value = float(updates["risk_threshold"])
        if value < 0 or value > 1:
            return {"error": "Risk eşiği 0 ile 1 arasında olmalıdır."}
        mandate.risk_threshold = value

    if "single_use_tokens" in updates:
        mandate.single_use_tokens = bool(updates["single_use_tokens"])

    persistence.save_mandate(
        mandate.mandate_id,
        mandate.user_id,
        mandate.to_dict(),
        mandate.status,
        now_ms(),
    )

    STATE.audit.record("mandate:" + mandate.mandate_id, "mandate_updated", {
        "updated_at": now_ms(),
        "mandate_id": mandate.mandate_id,
        "updates": updates,
        "mandate": mandate.to_dict(),
    })

    return {
        "mandate": asdict(mandate),
        "status": mandate.status,
        "updated": True,
    }


def approve_mandate(body: dict) -> dict:
    mandate_id = body.get("mandate_id")
    mandate = STATE.mandates.get(mandate_id)

    if not mandate:
        return {"error": "Mandate bulunamadı."}

    if mandate.status == "active":
        return {
            "mandate": asdict(mandate),
            "status": "active",
            "note": "Mandate zaten aktif.",
        }

    for m in STATE.mandates.values():
        if m.user_id == mandate.user_id and m.status == "active":
            m.status = "revoked"
            persistence.save_mandate(
                m.mandate_id,
                m.user_id,
                m.to_dict(),
                m.status,
                now_ms(),
            )

    mandate.status = "active"
    persistence.save_mandate(
        mandate.mandate_id,
        mandate.user_id,
        mandate.to_dict(),
        mandate.status,
        now_ms(),
    )

    STATE.audit.record("mandate:" + mandate.mandate_id, "mandate_approved", {
        "approved_at": now_ms(),
        "mandate_id": mandate.mandate_id,
    })

    return {"mandate": asdict(mandate), "status": "active"}


def agent_request(body: dict) -> dict:
    scenario = body.get("scenario", "safe")
    user_id = body.get("user_id", DEFAULT_USER)

    if scenario not in [s["key"] for s in list_scenarios()]:
        return {"error": f"Bilinmeyen senaryo: {scenario}"}

    reuse = STATE.last_issued_token_id if scenario == "token_reuse" else None
    return build_request(scenario, user_id, reused_token_id=reuse)


def evaluate_transaction(body: dict) -> dict:
    transaction = body.get("transaction")
    if not transaction:
        return {"error": "Transaction payload eksik."}

    return evaluate_transaction_core(transaction)


def security_scan(body: dict) -> dict:
    return scan_text(body.get("text", ""))


def set_risk_threshold(body: dict) -> dict:
    import services.risk_model as rm

    profile = body.get("profile", "balanced")
    presets = {
        "strict": {"step": 0.30, "review": 0.55, "decline": 0.70},
        "balanced": {"step": 0.40, "review": 0.70, "decline": 0.85},
        "lenient": {"step": 0.55, "review": 0.80, "decline": 0.92},
    }

    if profile not in presets:
        return {"error": f"Bilinmeyen profil: {profile}"}

    rm.set_thresholds(presets[profile])
    return {"profile": profile, "thresholds": presets[profile]}


def tamper_token(body: dict) -> dict:
    token_id = body.get("token_id") or STATE.last_issued_token_id

    if not token_id:
        return {"error": "Kurcalanacak token yok. Önce onaylı bir işlem çalıştırın."}

    new_amount = float(body.get("new_amount", 999999))
    result = STATE.tokens.tamper_test(token_id, new_amount)

    if result.get("ok"):
        token = STATE.tokens.get(token_id)
        STATE.audit.record(
            token.transaction_id if token else "tamper",
            "token_tamper_test",
            {
                "token_id": token_id,
                "original_amount": result["original_amount"],
                "tampered_amount": result["tampered_amount"],
                "signature_valid_after_tamper": result["valid_after"],
                "redeem_blocked": result["redeem_blocked"],
            },
        )

    return result


def resolve_stepup(body: dict) -> dict:
    tx_id = body.get("transaction_id")
    approved = body.get("approved", False)
    pending = STATE.pending_stepups.get(tx_id)

    if not pending:
        return {"error": "Bekleyen step-up işlemi bulunamadı."}

    if not approved:
        STATE.audit.record(tx_id, "stepup_resolved", {
            "resolution": "rejected",
            "final_decision": "decline",
            "explanation": "Kullanıcı ek onayı reddetti; işlem iptal edildi.",
        })
        STATE.audit.record(tx_id, "decision", {
            "final_decision": "decline",
            "explanation": "Kullanıcı ek onayı reddetti; işlem iptal edildi.",
        })
        del STATE.pending_stepups[tx_id]
        return {
            "final_decision": "decline",
            "explanation": "Kullanıcı ek onayı reddetti; işlem iptal edildi.",
        }

    tx = STATE.transactions[tx_id]
    mandate = STATE.active_mandate_for(tx.user_id)
    merchant = STATE.merchants.get(tx.merchant_id)

    token = STATE.tokens.issue(tx, mandate, merchant)
    STATE.last_issued_token_id = token.token_id

    mandate.spent_so_far += tx.amount
    STATE.tokens.redeem(token.token_id)

    token_dict = asdict(token)

    STATE.audit.record(tx_id, "stepup_resolved", {
        "resolution": "approved",
        "final_decision": "approve",
        "explanation": "Kullanıcı ek onayı verdi; işlem onaylandı.",
    })
    STATE.audit.record(tx_id, "token_issued", token_dict)
    STATE.audit.record(tx_id, "decision", {
        "final_decision": "approve",
        "explanation": "Kullanıcı ek onayı verdi; işlem onaylandı.",
    })

    del STATE.pending_stepups[tx_id]

    return {
        "final_decision": "approve",
        "token": token_dict,
        "explanation": "Kullanıcı ek onayı verdi; işlem onaylandı.",
    }


def analytics() -> dict:
    txns = STATE.audit.grouped_by_transaction()

    decisions = {"approve": 0, "step-up": 0, "review": 0, "decline": 0}
    rule_hits: dict[str, int] = {}
    risk_buckets = {"low": 0, "medium": 0, "high": 0}
    risk_scores: list[float] = []
    total_authorized = 0.0
    recent_transactions: list[dict] = []

    def pct(count: int, total_count: int) -> float:
        return round((count / total_count * 100), 1) if total_count else 0.0

    def decision_key(final_decision: str | None) -> str:
        return {
            "approve": "approved",
            "step-up": "step_up",
            "review": "review",
            "decline": "denied",
        }.get(final_decision or "", "unknown")

    for it in txns:
        fd = it.get("final_decision")
        if fd in decisions:
            decisions[fd] += 1

        tx_req = {}
        risk_info = {}
        policy_info = {}

        for ev in it.get("events", []):
            d = ev.get("details", {})
            et = ev.get("event_type")

            if et == "transaction_request":
                tx_req = d

            elif et == "policy_evaluation":
                policy_info = d
                for f in d.get("failed", []):
                    r = f.get("rule", "?")
                    rule_hits[r] = rule_hits.get(r, 0) + 1
                for w in d.get("warnings", []):
                    r = w.get("rule", "?")
                    rule_hits[r] = rule_hits.get(r, 0) + 1

            elif et == "risk_scoring":
                risk_info = d
                lvl = d.get("risk_level")
                if lvl in risk_buckets:
                    risk_buckets[lvl] += 1
                if isinstance(d.get("risk_score"), (int, float)):
                    risk_scores.append(d["risk_score"])

            elif et == "token_issued":
                total_authorized += d.get("max_amount", 0)

        if fd not in decisions:
            continue

        recent_transactions.append({
            "transaction_id": it.get("transaction_id"),
            "timestamp": it.get("timestamp"),
            "final_decision": fd,
            "decision_key": decision_key(fd),
            "merchant": tx_req.get("merchant"),
            "amount": tx_req.get("amount"),
            "currency": tx_req.get("currency"),
            "category": tx_req.get("category"),
            "cart": tx_req.get("cart"),
            "risk_score": risk_info.get("risk_score"),
            "risk_level": risk_info.get("risk_level"),
            "suggested_action": risk_info.get("suggested_action"),
            "policy_preliminary_decision": policy_info.get("preliminary_decision"),
            "failed_rule_count": len(policy_info.get("failed", [])) if policy_info else 0,
            "warning_count": len(policy_info.get("warnings", [])) if policy_info else 0,
            "explanation": it.get("explanation"),
        })

    total = sum(decisions.values())
    approve_rate = pct(decisions["approve"], total)
    block_rate = pct(decisions["decline"], total)
    stepup_rate = pct(decisions["step-up"], total)
    review_rate = pct(decisions["review"], total)
    avg_risk = (sum(risk_scores) / len(risk_scores)) if risk_scores else 0

    top_rules = sorted(rule_hits.items(), key=lambda x: -x[1])

    decision_distribution = [
        {
            "key": "approved",
            "source": "approve",
            "label": "Approved",
            "label_tr": "Onaylandı",
            "count": decisions["approve"],
            "percentage": approve_rate,
        },
        {
            "key": "step_up",
            "source": "step-up",
            "label": "Step-up",
            "label_tr": "Ek Onay",
            "count": decisions["step-up"],
            "percentage": stepup_rate,
        },
        {
            "key": "review",
            "source": "review",
            "label": "Review",
            "label_tr": "İnceleme",
            "count": decisions["review"],
            "percentage": review_rate,
        },
        {
            "key": "denied",
            "source": "decline",
            "label": "Denied",
            "label_tr": "Reddedildi",
            "count": decisions["decline"],
            "percentage": block_rate,
        },
    ]

    risk_total = sum(risk_buckets.values())
    risk_distribution = [
        {
            "key": "low",
            "label": "Low",
            "label_tr": "Düşük",
            "count": risk_buckets["low"],
            "percentage": pct(risk_buckets["low"], risk_total),
        },
        {
            "key": "medium",
            "label": "Medium",
            "label_tr": "Orta",
            "count": risk_buckets["medium"],
            "percentage": pct(risk_buckets["medium"], risk_total),
        },
        {
            "key": "high",
            "label": "High",
            "label_tr": "Yüksek",
            "count": risk_buckets["high"],
            "percentage": pct(risk_buckets["high"], risk_total),
        },
    ]

    return {
        "total_transactions": total,
        "decisions": decisions,
        "approve_rate": approve_rate,
        "block_rate": block_rate,
        "avg_risk": round(avg_risk, 3),
        "risk_buckets": risk_buckets,
        "total_authorized": total_authorized,
        "top_rules": [
            {"rule": r, "label": RULE_TR.get(r, r), "count": c}
            for r, c in top_rules
        ],
        "generated_at": now_ms(),
        "summary": {
            "total_transactions": total,
            "approved": decisions["approve"],
            "denied": decisions["decline"],
            "step_up": decisions["step-up"],
            "review": decisions["review"],
            "approval_rate": approve_rate,
            "denial_rate": block_rate,
            "step_up_rate": stepup_rate,
            "review_rate": review_rate,
            "avg_risk": round(avg_risk, 3),
            "total_authorized": total_authorized,
        },
        "decision_distribution": decision_distribution,
        "risk_distribution": risk_distribution,
        "recent_transactions": recent_transactions[:10],
    }