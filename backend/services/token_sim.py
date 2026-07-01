"""
IntentPay AI - Token Simulation Module (Kriptografik İmzalı)
============================================================
Gerçek ödeme YOK. Onaylanan işlemler için tek kullanımlık, sınırlandırılmış,
KRİPTOGRAFİK OLARAK İMZALANMIŞ ödeme yetkisi (token) üretir.

Güvenlik özellikleri:
  - Her token, alanları (id, tutar, kategori, satıcı, süre) üzerinden HMAC-SHA256
    ile imzalanır. Token kurcalanırsa (ör. tutar değiştirilirse) imza doğrulaması
    çöker -> token reddedilir.
  - Tek kullanımlık: redeem sonrası status=used, replay engellenir.
  - Süre dolumu: valid_until geçilirse status=expired.
  - İptal: revoke() ile bir token geçersiz kılınabilir.
  - Tüm token durumu SQLite'a yazılır (kalıcılık).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import asdict

from models.schema import (
    Token, TransactionRequest, Mandate, Merchant, now_ms, _id,
)
from services import persistence

# Demo imzalama anahtarı. Production'da bir KMS/secret manager'dan gelirdi.
_SIGNING_KEY = os.environ.get("INTENTPAY_TOKEN_KEY", "intentpay-demo-hmac-key-2025").encode()


def _signing_payload(t: Token) -> bytes:
    """İmzalanacak kanonik alanlar. Bu alanlardan biri değişirse imza bozulur."""
    return f"{t.token_id}|{t.transaction_id}|{t.user_id}|{t.agent_id}|" \
           f"{t.merchant_id}|{t.max_amount}|{t.currency}|{t.category}|" \
           f"{t.valid_until}|{t.single_use}".encode()


def sign_token(t: Token) -> str:
    return hmac.new(_SIGNING_KEY, _signing_payload(t), hashlib.sha256).hexdigest()


def verify_token(t: Token) -> bool:
    """İmza geçerli mi? Token kurcalandıysa False döner."""
    expected = sign_token(t)
    return hmac.compare_digest(expected, t.signature or "")


class TokenStore:
    """Token deposu. Bellek + SQLite (kalıcı). Production'da güvenli vault olurdu."""

    def __init__(self) -> None:
        self._tokens: dict[str, Token] = {}

    # --- restore from disk ---
    def load(self, rows: list[dict]) -> None:
        for r in rows:
            d = r["data"]
            self._tokens[r["token_id"]] = Token(**d)

    def _persist(self, t: Token) -> None:
        persistence.save_token(t.token_id, asdict(t), t.status, now_ms())

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
            issued_at=now_ms(),
        )
        token.signature = sign_token(token)     # kriptografik imza
        self._tokens[token.token_id] = token
        self._persist(token)
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
        """Token'ı kullan. İmza doğrulanır, tek kullanımlıksa bir daha kullanılamaz."""
        t = self._tokens.get(token_id)
        if t is None:
            return {"ok": False, "reason": "Token bulunamadı."}
        if not verify_token(t):
            t.status = "revoked"
            self._persist(t)
            return {"ok": False, "reason": "Token imzası geçersiz (kurcalanmış olabilir)."}
        if t.status != "active":
            return {"ok": False, "reason": f"Token kullanılamaz (durum: {t.status})."}
        if now_ms() > t.valid_until:
            t.status = "expired"
            self._persist(t)
            return {"ok": False, "reason": "Token'ın geçerlilik süresi dolmuş."}
        if t.single_use:
            t.status = "used"
            self._persist(t)
        return {"ok": True, "token": t}

    def revoke(self, token_id: str) -> dict:
        t = self._tokens.get(token_id)
        if t is None:
            return {"ok": False, "reason": "Token bulunamadı."}
        t.status = "revoked"
        self._persist(t)
        return {"ok": True, "token": t}

    def tamper_test(self, token_id: str, new_amount: float) -> dict:
        """
        DEMO: Bir token'ın tutarını kurcalayıp imza doğrulamasının çöktüğünü gösterir.
        Gerçek saldırı simülasyonu — token verisi değişti ama imza eski kaldı.
        """
        t = self._tokens.get(token_id)
        if t is None:
            return {"ok": False, "reason": "Token bulunamadı."}
        original = t.max_amount
        valid_before = verify_token(t)
        t.max_amount = new_amount            # kurcala: tutarı değiştir, imza eski kalsın
        valid_after = verify_token(t)
        redeem = self.redeem(token_id)       # redeem denemesi -> imza çökmeli
        t.max_amount = original              # durumu geri al (demo tekrar çalışsın)
        if t.status == "revoked":
            t.status = "active"
        self._persist(t)
        return {
            "ok": True,
            "original_amount": original,
            "tampered_amount": new_amount,
            "valid_before": valid_before,
            "valid_after": valid_after,
            "redeem_blocked": not redeem["ok"],
            "reason": redeem.get("reason"),
        }

    def all(self) -> list[dict]:
        return [asdict(t) for t in self._tokens.values()]