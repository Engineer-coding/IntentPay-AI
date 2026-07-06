"""
IntentPay AI - Application State
================================
Demo uygulamasının ortak runtime state katmanı.

FastAPI, servisler ve legacy stdlib server aynı STATE nesnesini kullanır.
"""
from __future__ import annotations

from models.schema import Mandate, TransactionRequest
from data.synthetic import seed_users, seed_agents, seed_merchants, get_products
from services.token_sim import TokenStore
from services.audit_log import AuditTrail
from services import persistence


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
        self.tx_timestamps: list[int] = []
        self.pending_stepups: dict[str, dict] = {}

    def restore(self) -> None:
        """Sunucu açılışında kalıcı durumu diskten geri yükler."""
        data = persistence.load_all()

        for m in data["mandates"]:
            try:
                self.mandates[m["mandate_id"]] = Mandate(
                    **{k: v for k, v in m["data"].items() if not k.startswith("_")}
                )
            except Exception:
                pass

        for t in data["transactions"]:
            try:
                self.transactions[t["transaction_id"]] = TransactionRequest(**t["data"])
            except Exception:
                pass

        self.tokens.load(data["tokens"])
        self.audit.load(data["audit"])

    def active_mandate_for(self, user_id: str) -> Mandate | None:
        for mandate in self.mandates.values():
            if mandate.user_id == user_id and mandate.status == "active":
                return mandate
        return None


STATE = AppState()
DEFAULT_USER = "u_acme"
