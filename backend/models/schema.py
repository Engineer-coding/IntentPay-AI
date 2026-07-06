"""
IntentPay AI - Veri Modelleri
==============================
Tüm temel varlıkların (entity) şema tanımları. Production değil; hackathon MVP
için sade dataclass'lar kullanılır. Hiçbir gerçek finansal veri yoktur.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
#  Core entities
# --------------------------------------------------------------------------- #
@dataclass
class User:
    user_id: str
    company_name: str
    user_type: str                       # "sme" | "enterprise"
    average_transaction_amount: float    # TRY
    risk_profile: str                    # "low" | "medium" | "high"


@dataclass
class Agent:
    agent_id: str
    user_id: str
    agent_name: str
    trust_level: str                     # "low" | "medium" | "high"
    created_at: int
    status: str = "active"               # active | suspended

    @property
    def age_days(self) -> float:
        return (now_ms() - self.created_at) / 86_400_000


@dataclass
class Merchant:
    merchant_id: str
    merchant_name: str
    category: str
    trust_score: float                   # 0..1
    is_approved: bool
    country: str = "TR"
    mcc_code: str = "5999"               # Merchant Category Code


@dataclass
class Mandate:
    """Doğal dil talimatın yapılandırılmış, uygulanabilir kurallara dönüşmüş hali."""
    mandate_id: str
    user_id: str
    original_intent_text: str
    max_amount: float                    # işlem başına maksimum
    total_limit: float                   # toplam harcama tavanı
    currency: str
    allowed_categories: list[str]
    blocked_categories: list[str]
    allowed_merchants: list[str]         # boşsa "tümü (onaylı)" demektir
    requires_approval_for_new_merchant: bool
    valid_from: int
    valid_until: int
    risk_threshold: float = 0.7          # bunun üstü step-up/review tetikler
    single_use_tokens: bool = True
    status: str = "pending"              # pending | active | revoked | expired
    spent_so_far: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TransactionRequest:
    """AI ajanının başlatmak istediği ödeme isteği."""
    transaction_id: str
    user_id: str
    agent_id: str
    merchant_id: str
    amount: float
    currency: str
    category: str
    timestamp: int
    cart_description: str = ""
    note: str = ""
    reused_token_id: Optional[str] = None   # replay senaryosu için
    status: str = "pending"


@dataclass
class RiskResult:
    transaction_id: str
    risk_score: float                    # 0..1
    risk_level: str                      # low | medium | high
    top_risk_factors: list[dict]         # [{factor, contribution, value}]
    suggested_action: str                # approve | step-up | review | decline


@dataclass
class PolicyResult:
    passed_rules: list[str]
    failed_rules: list[dict]             # [{rule, reason}]
    warnings: list[dict]                 # [{rule, reason}] -> step-up tetikleyebilir
    preliminary_decision: str            # approve | step-up | review | decline


@dataclass
class Token:
    token_id: str
    transaction_id: str
    user_id: str
    agent_id: str
    merchant_id: str
    max_amount: float
    currency: str
    category: str
    valid_until: int
    single_use: bool = True
    status: str = "active"               # active | used | expired | revoked
    signature: str = ""                  # HMAC imzası (kriptografik bütünlük)
    issued_at: int = 0


@dataclass
class Decision:
    transaction_id: str
    final_decision: str                  # approve | step-up | decline | review
    explanation: str
    explanation_factors: list[str]
    policy_result: dict
    risk_result: dict
    token: Optional[dict]
    timestamp: int


@dataclass
class AuditLog:
    log_id: str
    transaction_id: str
    event_type: str
    details: dict
    timestamp: int = field(default_factory=now_ms)


# --------------------------------------------------------------------------- #
#  Category vocabulary (demo marketplace ile hizalı)
# --------------------------------------------------------------------------- #
CATEGORIES = {
    "office_furniture": "Ofis Mobilyası",
    "office_supplies": "Ofis Malzemeleri",
    "cleaning": "Temizlik Malzemeleri",
    "electronics": "Elektronik",
    "gift_cards": "Hediye Kartı",
}