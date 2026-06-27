"""
IntentPay AI - Policy Engine (Deterministik)
============================================
Sistemin en kritik güvenlik bileşeni. İşlem isteğini mandate kurallarıyla
karşılaştırır. AYNI girdi -> AYNI çıktı (deterministik). LLM veya rastgelelik YOK.

Kontroller:
  - amount limit check
  - total spending limit check
  - blocked category check
  - allowed category check
  - merchant approval check
  - new-merchant step-up check
  - mandate validity (date) check
  - mandate status check
  - agent authorization check
  - token reuse check
"""
from __future__ import annotations

from models.schema import (
    Mandate, Merchant, Agent, TransactionRequest, PolicyResult, now_ms, CATEGORIES,
)


def evaluate_policy(
    tx: TransactionRequest,
    mandate: Mandate,
    merchant: Merchant,
    agent: Agent,
    used_token_ids: set[str],
) -> PolicyResult:
    passed: list[str] = []
    failed: list[dict] = []
    warnings: list[dict] = []

    # --- 1. mandate aktif mi ---
    if mandate.status != "active":
        failed.append({"rule": "mandate_status",
                       "reason": f"Mandate aktif değil (durum: {mandate.status})."})
    else:
        passed.append("mandate_status")

    # --- 2. geçerlilik süresi ---
    now = now_ms()
    if now > mandate.valid_until:
        failed.append({"rule": "validity_date",
                       "reason": "Mandate'in geçerlilik süresi dolmuş."})
    elif now < mandate.valid_from:
        failed.append({"rule": "validity_date",
                       "reason": "Mandate henüz geçerlilik başlangıcına ulaşmadı."})
    else:
        passed.append("validity_date")

    # --- 3. ajan yetkisi ---
    if agent.status != "active":
        failed.append({"rule": "agent_authorization",
                       "reason": f"Ajan ödeme başlatmaya yetkili değil (durum: {agent.status})."})
    elif agent.user_id != mandate.user_id:
        failed.append({"rule": "agent_authorization",
                       "reason": "Ajan bu kullanıcının mandate'ine bağlı değil."})
    else:
        passed.append("agent_authorization")

    # --- 4. token tekrar kullanımı (replay) ---
    if tx.reused_token_id:
        if tx.reused_token_id in used_token_ids:
            failed.append({"rule": "token_reuse",
                           "reason": "Bu ödeme token'ı daha önce kullanılmış (replay denemesi)."})
        else:
            warnings.append({"rule": "token_reuse",
                             "reason": "İşlem mevcut bir token'a referans veriyor; doğrulanmalı."})
    else:
        passed.append("token_reuse")

    # --- 5. yasaklı kategori ---
    if tx.category in mandate.blocked_categories:
        cat_tr = CATEGORIES.get(tx.category, tx.category)
        failed.append({"rule": "blocked_category",
                       "reason": f"'{cat_tr}' kategorisi mandate tarafından yasaklanmış."})
    else:
        passed.append("blocked_category")

    # --- 6. izin verilen kategori ---
    if mandate.allowed_categories and tx.category not in mandate.allowed_categories:
        cat_tr = CATEGORIES.get(tx.category, tx.category)
        allowed_tr = ", ".join(CATEGORIES.get(c, c) for c in mandate.allowed_categories)
        failed.append({"rule": "allowed_category",
                       "reason": f"'{cat_tr}' izin verilen kategoriler arasında değil "
                                 f"(izinli: {allowed_tr})."})
    else:
        passed.append("allowed_category")

    # --- 7. işlem başına tutar limiti ---
    if tx.amount > mandate.max_amount:
        over = tx.amount - mandate.max_amount
        ratio = over / mandate.max_amount if mandate.max_amount else 1.0
        if ratio <= 0.25:    # %25'e kadar aşım -> step-up
            warnings.append({"rule": "amount_limit",
                             "reason": f"İşlem tutarı limiti %{ratio*100:.0f} aşıyor "
                                       f"({tx.amount:,.0f} > {mandate.max_amount:,.0f} TL). Ek onay gerekli."})
        else:
            failed.append({"rule": "amount_limit",
                           "reason": f"İşlem tutarı limiti büyük ölçüde aşıyor "
                                     f"({tx.amount:,.0f} > {mandate.max_amount:,.0f} TL)."})
    else:
        passed.append("amount_limit")

    # --- 8. toplam harcama tavanı ---
    if mandate.spent_so_far + tx.amount > mandate.total_limit:
        failed.append({"rule": "total_spending_limit",
                       "reason": f"Toplam harcama tavanı aşılır "
                                 f"(mevcut {mandate.spent_so_far:,.0f} + {tx.amount:,.0f} "
                                 f"> {mandate.total_limit:,.0f} TL)."})
    else:
        passed.append("total_spending_limit")

    # --- 9. satıcı onayı ---
    approved_only = "__approved_only__" in mandate.allowed_merchants
    if approved_only and not merchant.is_approved:
        failed.append({"rule": "merchant_approval",
                       "reason": f"'{merchant.merchant_name}' onaylı satıcı listesinde değil."})
    elif (mandate.allowed_merchants and "__approved_only__" not in mandate.allowed_merchants
          and merchant.merchant_id not in mandate.allowed_merchants):
        failed.append({"rule": "merchant_approval",
                       "reason": f"'{merchant.merchant_name}' izin verilen satıcılar arasında değil."})
    else:
        passed.append("merchant_approval")

    # --- 10. yeni satıcı step-up ---
    if mandate.requires_approval_for_new_merchant and not merchant.is_approved:
        warnings.append({"rule": "new_merchant_stepup",
                         "reason": f"'{merchant.merchant_name}' yeni/onaysız bir satıcı; "
                                   f"kullanıcı ek onayı gerekli."})
    else:
        passed.append("new_merchant_stepup")

    # --- nihai ön karar ---
    if failed:
        decision = "decline"
    elif warnings:
        decision = "step-up"
    else:
        decision = "approve"

    return PolicyResult(
        passed_rules=passed,
        failed_rules=failed,
        warnings=warnings,
        preliminary_decision=decision,
    )