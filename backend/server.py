"""
IntentPay AI - Backend API
==========================
Sıfır-bağımlılık HTTP sunucusu (Python stdlib). Tüm servisleri orkestre eder.

Endpointler:
  GET  /api/bootstrap            -> kullanıcılar, satıcılar, ajanlar, ürünler, senaryolar
  POST /api/intent/parse         -> doğal dil -> mandate (onay bekler)
  POST /api/mandate/approve      -> mandate'i aktifleştir
  POST /api/agent/request        -> ajan simülatörü ödeme isteği üretir
  POST /api/transaction/evaluate -> policy + risk + karar + token + audit
  GET  /api/audit                -> denetim kayıtları (işlem bazında gruplu)
  GET  /api/tokens               -> üretilmiş tokenlar
  GET  /api/health

Tüm durum bellekte (demo). Gerçek ödeme / kart / banka YOK.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(__file__))

from models.schema import (
    Mandate, TransactionRequest, now_ms,
)
from data.synthetic import (
    seed_users, seed_agents, seed_merchants, get_products,
)
from services.intent_parser import parse_intent
from services.policy_engine import evaluate_policy
from services.risk_model import feature_vector, score_transaction
from services.decision_engine import decide
from services.token_sim import TokenStore
from services.audit_log import AuditTrail
from services.agent_simulator import build_request, list_scenarios
from services.attack_detector import scan_text, sanitize_mandate
from services import persistence


# --------------------------------------------------------------------------- #
#  Shared application state
# --------------------------------------------------------------------------- #
from services.app_state import AppState, STATE, DEFAULT_USER


# --------------------------------------------------------------------------- #
#  Core orchestration: evaluate a transaction end-to-end
# --------------------------------------------------------------------------- #
def evaluate_transaction(tx_dict: dict) -> dict:
    tx = TransactionRequest(**tx_dict)
    STATE.transactions[tx.transaction_id] = tx
    persistence.save_transaction(tx.transaction_id, tx.user_id, tx.__dict__,
                                 tx.status, now_ms())

    user = STATE.users[tx.user_id]
    agent = STATE.agents.get(tx.agent_id)
    merchant = STATE.merchants.get(tx.merchant_id)
    mandate = STATE.active_mandate_for(tx.user_id)

    STATE.audit.record(tx.transaction_id, "transaction_request", {
        "agent_id": tx.agent_id, "merchant": merchant.merchant_name if merchant else None,
        "amount": tx.amount, "currency": tx.currency, "category": tx.category,
        "cart": tx.cart_description, "note": tx.note,
    })

    if mandate is None:
        result = {"error": "Aktif mandate yok. Önce talimat girip onaylayın."}
        STATE.audit.record(tx.transaction_id, "decision",
                           {"final_decision": "decline", "explanation": result["error"]})
        return result

    # --- token reuse tespiti ---
    token_already_used = bool(tx.reused_token_id and STATE.tokens.is_used(tx.reused_token_id))

    # --- velocity: bu işlemden önceki son 60sn işlem sayısı ---
    now = now_ms()
    velocity_count = sum(1 for t in STATE.tx_timestamps if t >= now - 60_000)
    STATE.tx_timestamps.append(now)

    # --- 1. Policy Engine ---
    policy = evaluate_policy(tx, mandate, merchant, agent, STATE.tokens.used_ids(),
                             recent_tx_timestamps=STATE.tx_timestamps[:-1])
    STATE.audit.record(tx.transaction_id, "policy_evaluation", {
        "preliminary_decision": policy.preliminary_decision,
        "passed": policy.passed_rules,
        "failed": policy.failed_rules,
        "warnings": policy.warnings,
    })

    # --- 2. Risk Model ---
    feats = feature_vector(
        tx, mandate, merchant, agent, user,
        policy_failed_count=len(policy.failed_rules),
        token_already_used=token_already_used,
        velocity_count=velocity_count,
    )
    risk = score_transaction(feats, tx.transaction_id)
    STATE.audit.record(tx.transaction_id, "risk_scoring", {
        "risk_score": risk.risk_score, "risk_level": risk.risk_level,
        "suggested_action": risk.suggested_action,
        "top_risk_factors": risk.top_risk_factors,
        "features": feats, "mode": risk.__dict__.get("_mode"),
    })

    # --- 3. Decision ---
    decision = decide(policy, risk, tx.transaction_id)

    # --- 4. Token simulation (yalnızca approve) ---
    token_dict = None
    if decision.final_decision == "approve":
        token = STATE.tokens.issue(tx, mandate, merchant)
        STATE.last_issued_token_id = token.token_id
        mandate.spent_so_far += tx.amount
        # Tek kullanımlık token hemen redeem edilir (kullanıldı işaretlenir) ->
        # böylece sonraki replay denemesi token_reuse kuralında yakalanır.
        STATE.tokens.redeem(token.token_id)
        from dataclasses import asdict
        token_dict = asdict(token)
        decision.token = token_dict
        STATE.audit.record(tx.transaction_id, "token_issued", token_dict)
    elif decision.final_decision == "step-up":
        # Step-up: işlem kullanıcı onayı bekler (pending). Token henüz üretilmez.
        STATE.pending_stepups[tx.transaction_id] = {"amount": tx.amount}

    # --- 5. Decision audit ---
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


# --------------------------------------------------------------------------- #
#  HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):           # sessiz log
        pass

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            self._send(200, {"status": "ok", "time": now_ms()})
        elif path == "/api/bootstrap":
            self._send(200, self._bootstrap())
        elif path == "/api/audit":
            self._send(200, {"transactions": STATE.audit.grouped_by_transaction(),
                             "raw": STATE.audit.all()})
        elif path == "/api/tokens":
            self._send(200, {"tokens": STATE.tokens.all()})
        elif path == "/api/analytics":
            self._send(200, self._analytics())
        elif path == "/api/persistence":
            self._send(200, persistence.stats())
        elif path.startswith("/api/"):
            self._send(404, {"error": "not found"})
        else:
            self._serve_static(path)

    def _serve_static(self, path):
        """Frontend dosyalarını servis eder (demo: tek komutla çalışsın)."""
        fe_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(fe_dir, rel))
        if not full.startswith(os.path.normpath(fe_dir)) or not os.path.isfile(full):
            self.send_response(404); self.end_headers(); return
        ctype = ("text/html" if full.endswith(".html")
                 else "text/javascript" if full.endswith(".jsx") or full.endswith(".js")
                 else "text/css" if full.endswith(".css")
                 else "application/octet-stream")
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
            if path == "/api/intent/parse":
                self._send(200, self._parse(body))
            elif path == "/api/mandate/approve":
                self._send(200, self._approve(body))
            elif path == "/api/agent/request":
                self._send(200, self._agent_request(body))
            elif path == "/api/transaction/evaluate":
                self._send(200, evaluate_transaction(body["transaction"]))
            elif path == "/api/security/scan":
                self._send(200, scan_text(body.get("text", "")))
            elif path == "/api/stepup/resolve":
                self._send(200, self._resolve_stepup(body))
            elif path == "/api/token/tamper":
                self._send(200, self._tamper_token(body))
            elif path == "/api/risk/threshold":
                self._send(200, self._set_threshold(body))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._send(500, {"error": str(e)})

    # --- handlers ---
    def _bootstrap(self) -> dict:
        from dataclasses import asdict
        return {
            "users": {k: asdict(v) for k, v in STATE.users.items()},
            "agents": {k: {**asdict(v), "age_days": round(v.age_days, 1)}
                       for k, v in STATE.agents.items()},
            "merchants": {k: asdict(v) for k, v in STATE.merchants.items()},
            "products": STATE.products,
            "scenarios": list_scenarios(),
            "default_user": DEFAULT_USER,
            "categories": __import__("models.schema", fromlist=["CATEGORIES"]).CATEGORIES,
        }

    def _parse(self, body: dict) -> dict:
        text = (body.get("text") or "").strip()
        user_id = body.get("user_id", DEFAULT_USER)

        # --- giriş doğrulama (uç durum savunması) ---
        if not text:
            return {"error": "Talimat metni boş olamaz."}
        if len(text) > 2000:
            return {"error": "Talimat çok uzun (en fazla 2000 karakter)."}
        if user_id not in STATE.users:
            return {"error": f"Bilinmeyen kullanıcı: {user_id}"}

        # --- güvenlik taraması: talimata gizlenmiş manipülasyon var mı? ---
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

        result = parse_intent(text, user_id)
        # normal talimatlarda ek güvenlik için mandate sınırları normalize edilir
        result["mandate"] = sanitize_mandate(result["mandate"], scan)
        result["security_scan"] = scan

        mandate = Mandate(**{k: v for k, v in result["mandate"].items()
                             if not k.startswith("_")})
        STATE.mandates[mandate.mandate_id] = mandate
        persistence.save_mandate(mandate.mandate_id, mandate.user_id,
                                 mandate.to_dict(), mandate.status, now_ms())
        STATE.audit.record("mandate:" + mandate.mandate_id, "intent_parsed", {
            "original_intent": text, "parse_mode": result["parse_mode"],
            "security_scan": scan,
            "mandate": result["mandate"],
        })
        return result

    def _approve(self, body: dict) -> dict:
        mandate_id = body.get("mandate_id")
        mandate = STATE.mandates.get(mandate_id)
        if not mandate:
            return {"error": "Mandate bulunamadı."}
        if mandate.status == "active":
            from dataclasses import asdict
            return {"mandate": asdict(mandate), "status": "active",
                    "note": "Mandate zaten aktif."}
        # aynı kullanıcının diğer aktif mandate'lerini pasifleştir (tek aktif)
        for m in STATE.mandates.values():
            if m.user_id == mandate.user_id and m.status == "active":
                m.status = "revoked"
                persistence.save_mandate(m.mandate_id, m.user_id, m.to_dict(),
                                         m.status, now_ms())
        mandate.status = "active"
        persistence.save_mandate(mandate.mandate_id, mandate.user_id,
                                 mandate.to_dict(), mandate.status, now_ms())
        STATE.audit.record("mandate:" + mandate.mandate_id, "mandate_approved", {
            "approved_at": now_ms(), "mandate_id": mandate.mandate_id,
        })
        from dataclasses import asdict
        return {"mandate": asdict(mandate), "status": "active"}

    def _agent_request(self, body: dict) -> dict:
        scenario = body.get("scenario", "safe")
        user_id = body.get("user_id", DEFAULT_USER)
        if scenario not in [s["key"] for s in list_scenarios()]:
            return {"error": f"Bilinmeyen senaryo: {scenario}"}
        reuse = STATE.last_issued_token_id if scenario == "token_reuse" else None
        return build_request(scenario, user_id, reused_token_id=reuse)

    def _tamper_token(self, body: dict) -> dict:
        """DEMO: bir token'ı kurcalayıp HMAC imza doğrulamasının çöktüğünü gösterir."""
        token_id = body.get("token_id") or STATE.last_issued_token_id
        if not token_id:
            return {"error": "Kurcalanacak token yok. Önce onaylı bir işlem çalıştırın."}
        new_amount = float(body.get("new_amount", 999999))
        result = STATE.tokens.tamper_test(token_id, new_amount)
        if result.get("ok"):
            STATE.audit.record(
                STATE.tokens.get(token_id).transaction_id if STATE.tokens.get(token_id) else "tamper",
                "token_tamper_test", {
                    "token_id": token_id,
                    "original_amount": result["original_amount"],
                    "tampered_amount": result["tampered_amount"],
                    "signature_valid_after_tamper": result["valid_after"],
                    "redeem_blocked": result["redeem_blocked"],
                })
        return result

    def _set_threshold(self, body: dict) -> dict:
        """Risk toleransını ayarlar: strict / balanced / lenient."""
        import services.risk_model as rm
        profile = body.get("profile", "balanced")
        presets = {
            "strict":   {"step": 0.30, "review": 0.55, "decline": 0.70},
            "balanced": {"step": 0.40, "review": 0.70, "decline": 0.85},
            "lenient":  {"step": 0.55, "review": 0.80, "decline": 0.92},
        }
        if profile not in presets:
            return {"error": f"Bilinmeyen profil: {profile}"}
        rm.set_thresholds(presets[profile])
        return {"profile": profile, "thresholds": presets[profile]}

    def _resolve_stepup(self, body: dict) -> dict:
        """Step-up kararındaki bir işlemi kullanıcı onayı/reddi ile sonuçlandırır."""
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
            return {"final_decision": "decline",
                    "explanation": "Kullanıcı ek onayı reddetti; işlem iptal edildi."}

        # onaylandı -> token üret
        tx = STATE.transactions[tx_id]
        mandate = STATE.active_mandate_for(tx.user_id)
        merchant = STATE.merchants.get(tx.merchant_id)
        token = STATE.tokens.issue(tx, mandate, merchant)
        STATE.last_issued_token_id = token.token_id
        mandate.spent_so_far += tx.amount
        STATE.tokens.redeem(token.token_id)
        from dataclasses import asdict
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
        return {"final_decision": "approve", "token": token_dict,
                "explanation": "Kullanıcı ek onayı verdi; işlem onaylandı."}

    def _analytics(self) -> dict:
        """Audit verisinden karar/risk/kural/timeline istatistikleri üretir.

        Geriye dönük uyumluluk için eski alanlar korunur:
        - total_transactions
        - decisions
        - approve_rate
        - block_rate
        - avg_risk
        - risk_buckets
        - total_authorized
        - top_rules

        Dashboard için yeni alanlar eklenir:
        - generated_at
        - summary
        - decision_distribution
        - risk_distribution
        - recent_transactions
        """
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
        }

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
            # eski alanlar: mevcut frontend kırılmasın
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

            # yeni alanlar: zengin dashboard
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

def main():
    port = int(os.environ.get("PORT", 8787))
    # Kalıcılık: DB'yi hazırla. RESET_DB=1 ile temiz başla (demo).
    reset = os.environ.get("RESET_DB", "0") == "1"
    persistence.init_db(reset=reset)
    if not reset:
        STATE.restore()
        st = persistence.stats()
        print(f"Kalıcı durum yüklendi: {st['mandates']} mandate, "
              f"{st['transactions']} işlem, {st['tokens']} token, "
              f"{st['audit_events']} audit olayı.")
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"IntentPay AI backend çalışıyor:  http://localhost:{port}")
    print(f"Veritabanı: {persistence.stats()['db_path']}")
    server.serve_forever()


if __name__ == "__main__":
    main()