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


# --------------------------------------------------------------------------- #
#  In-memory application state
# --------------------------------------------------------------------------- #
class AppState:
    def __init__(self) -> None:
        self.users = seed_users()
        self.agents = seed_agents()
        self.merchants = seed_merchants()
        self.products = get_products()
        self.mandates: dict[str, Mandate] = {}
        self.transactions: dict[str, TransactionRequest] = {}
        self.tokens = TokenStore()
        self.audit = AuditTrail()
        self.last_issued_token_id: str | None = None

    def active_mandate_for(self, user_id: str) -> Mandate | None:
        for m in self.mandates.values():
            if m.user_id == user_id and m.status == "active":
                return m
        return None


STATE = AppState()
DEFAULT_USER = "u_acme"


# --------------------------------------------------------------------------- #
#  Core orchestration: evaluate a transaction end-to-end
# --------------------------------------------------------------------------- #
def evaluate_transaction(tx_dict: dict) -> dict:
    tx = TransactionRequest(**tx_dict)
    STATE.transactions[tx.transaction_id] = tx

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

    # --- 1. Policy Engine ---
    policy = evaluate_policy(tx, mandate, merchant, agent, STATE.tokens.used_ids())
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
        text = body.get("text", "")
        user_id = body.get("user_id", DEFAULT_USER)
        result = parse_intent(text, user_id)
        mandate = Mandate(**result["mandate"])
        STATE.mandates[mandate.mandate_id] = mandate
        STATE.audit.record("mandate:" + mandate.mandate_id, "intent_parsed", {
            "original_intent": text, "parse_mode": result["parse_mode"],
            "mandate": result["mandate"],
        })
        return result

    def _approve(self, body: dict) -> dict:
        mandate_id = body.get("mandate_id")
        mandate = STATE.mandates.get(mandate_id)
        if not mandate:
            return {"error": "Mandate bulunamadı."}
        # aynı kullanıcının diğer aktif mandate'lerini pasifleştir (tek aktif)
        for m in STATE.mandates.values():
            if m.user_id == mandate.user_id and m.status == "active":
                m.status = "revoked"
        mandate.status = "active"
        STATE.audit.record("mandate:" + mandate.mandate_id, "mandate_approved", {
            "approved_at": now_ms(), "mandate_id": mandate.mandate_id,
        })
        from dataclasses import asdict
        return {"mandate": asdict(mandate), "status": "active"}

    def _agent_request(self, body: dict) -> dict:
        scenario = body.get("scenario", "safe")
        user_id = body.get("user_id", DEFAULT_USER)
        reuse = STATE.last_issued_token_id if scenario == "token_reuse" else None
        return build_request(scenario, user_id, reused_token_id=reuse)


def main():
    port = int(os.environ.get("PORT", 8787))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"IntentPay AI backend çalışıyor:  http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()