"""
IntentPay AI - Token Simulation Module
======================================
Gerçek ödeme YOK. Onaylanan işlemler için tek kullanımlık, sınırlandırılmış ödeme
yetkisi (token) simüle eder. Token; satıcı, tutar, kategori ve süreyle sınırlıdır.

Kurallar:
  - Token tek kullanımlıktır; kullanıldıktan sonra status=used olur.
  - Süresi dolan token reddedilir (status=expired).
  - Aynı token ikinci kez kullanılamaz (replay engellenir).
"""
from __future__ import annotations

from models.schema import (
    Token, TransactionRequest, Mandate, Merchant, now_ms, _id,
)


class TokenStore:
    """Bellek içi token deposu (demo). Production'da güvenli vault olurdu."""

    def __init__(self) -> None:
        self._tokens: dict[str, Token] = {}

    def issue(self, tx: TransactionRequest, mandate: Mandate, merchant: Merchant) -> Token:
        token = Token(
            token_id=_id("tok"),
            transaction_id=tx.transaction_id,
            user_id=tx.user_id,
            agent_id=tx.agent_id,
            merchant_id=merchant.merchant_id,
            max_amount=tx.amount,
            currency=tx.currency,
            category=tx.category,
            valid_until=min(mandate.valid_until, now_ms() + 86_400_000),  # en fazla 24s
            single_use=mandate.single_use_tokens,
            status="active",
        )
        self._tokens[token.token_id] = token
        return token

    def get(self, token_id: str) -> Token | None:
        return self._tokens.get(token_id)

    def is_used(self, token_id: str) -> bool:
        t = self._tokens.get(token_id)
        return t is not None and t.status in ("used", "expired", "revoked")

    def used_ids(self) -> set[str]:
        return {tid for tid, t in self._tokens.items()
                if t.status in ("used", "expired", "revoked")}

    def redeem(self, token_id: str) -> dict:
        """Token'ı kullan. Tek kullanımlıksa bir daha kullanılamaz."""
        t = self._tokens.get(token_id)
        if t is None:
            return {"ok": False, "reason": "Token bulunamadı."}
        if t.status != "active":
            return {"ok": False, "reason": f"Token kullanılamaz (durum: {t.status})."}
        if now_ms() > t.valid_until:
            t.status = "expired"
            return {"ok": False, "reason": "Token'ın geçerlilik süresi dolmuş."}
        if t.single_use:
            t.status = "used"
        return {"ok": True, "token": t}

    def all(self) -> list[dict]:
        from dataclasses import asdict
        return [asdict(t) for t in self._tokens.values()]