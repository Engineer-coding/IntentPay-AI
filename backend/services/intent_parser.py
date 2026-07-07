"""
IntentPay AI - Intent Parser (Hibrit LLM + Kural Fallback)
==========================================================
Kullanıcının doğal dilde yazdığı ödeme talimatını yapılandırılmış Mandate'e çevirir.

GÜVENLİK PRENSİBİ: Bu katman ASLA nihai ödeme kararı vermez. Sadece doğal dili
kurallara çevirir. Çıktı kullanıcıya gösterilir ve onaylanmadan aktif olmaz.

Çalışma modu:
  - OPENAI_API_KEY ortam değişkeni varsa -> gerçek LLM ile parse (mode="llm")
  - yoksa                                -> deterministik kural tabanlı parse
  - LLM hata verirse                     -> deterministik kural fallback (mode="rule_fallback")
"""
from __future__ import annotations

import json
import os
import re
import time

from models.schema import Mandate, CATEGORIES, _id, now_ms

# Türkçe kategori anahtar kelimeleri -> kanonik kategori
_CATEGORY_KEYWORDS = {
    "office_furniture": ["sandalye", "koltuk", "masa", "mobilya", "dolap"],
    "office_supplies":  ["ofis malzeme", "kırtasiye", "kağıt", "kalem", "defter", "toner"],
    "cleaning":         ["temizlik", "deterjan", "hijyen"],
    "electronics":      ["elektronik", "laptop", "bilgisayar", "telefon", "tablet", "yazıcı"],
    "gift_cards":       ["hediye kart", "gift card", "hediye çeki"],
}

_DURATION_DAYS = {
    "gün": 1, "bugün": 1,
    "hafta": 7, "bu hafta": 7,
    "ay": 30, "bu ay": 30,
    "yıl": 365,
}


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def parse_intent(text: str, user_id: str) -> dict:
    """Doğal dil -> mandate (dict). Hangi modun kullanıldığını da döner."""
    if os.environ.get("OPENAI_API_KEY"):
        try:
            mandate, mode = _parse_with_llm(text, user_id), "llm"
        except Exception as e:
            print(
                "[intent_parser] LLM başarısız, kurala düşülüyor: "
                f"{type(e).__name__}: {e}"
            )
            mandate, mode = _parse_with_rules(text, user_id), "rule_fallback"
    else:
        mandate, mode = _parse_with_rules(text, user_id), "rule"

    return {"mandate": mandate.to_dict(), "parse_mode": mode}


# --------------------------------------------------------------------------- #
#  Rule-based parser (her zaman çalışır, sıfır bağımlılık)
# --------------------------------------------------------------------------- #
def _parse_with_rules(text: str, user_id: str) -> Mandate:
    low = text.lower()

    # --- tutar (TL / TRY) ---
    max_amount = _extract_amount(low)

    # --- kategoriler ---
    allowed, blocked = _extract_categories(low)

    # --- süre ---
    valid_days = _extract_duration(low)

    # --- yeni satıcı / onay koşulu ---
    requires_new_approval = any(
        k in low for k in ["onay iste", "ek onay", "benden onay", "yeni satıcı", "onaylı satıcı"]
    )
    only_approved = any(k in low for k in ["onaylı satıcı", "güvenilir satıcı", "güvenilir satıcılar"])

    now = now_ms()
    return Mandate(
        mandate_id=_id("man"),
        user_id=user_id,
        original_intent_text=text,
        max_amount=max_amount,
        total_limit=max_amount * 3,                # toplam tavan = işlem limitinin 3 katı
        currency="TRY",
        allowed_categories=allowed,
        blocked_categories=blocked,
        allowed_merchants=[] if not only_approved else ["__approved_only__"],
        requires_approval_for_new_merchant=requires_new_approval or only_approved,
        valid_from=now,
        valid_until=now + valid_days * 86_400_000,
        risk_threshold=0.7,
        single_use_tokens=True,
        status="pending",
    )


def _extract_amount(low: str) -> float:
    # "5.000 TL", "5000 tl", "10.000", "5 bin"
    m = re.search(r"([\d][\d\.\, ]*)\s*(tl|try|lira|₺)", low)
    if m:
        return _to_float(m.group(1))
    m = re.search(r"([\d]+)\s*bin", low)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"\b(\d{3,6})\b", low)
    return _to_float(m.group(1)) if m else 5000.0


def _to_float(s: str) -> float:
    s = s.strip().replace(" ", "")
    # Türkçe format: 5.000,50 -> binlik nokta, ondalık virgül
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "." in s and len(s.split(".")[-1]) == 3:
        s = s.replace(".", "")          # 5.000 -> 5000
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 5000.0


def _extract_categories(low: str) -> tuple[list[str], list[str]]:
    allowed, blocked = [], []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in low for kw in kws):
            # "elektronik alma", "X alma/yapma" -> yasak
            negated = any(
                re.search(rf"{re.escape(kw)}[^.]{{0,20}}\b(alma|alınmasın|yapma|yasak)", low)
                for kw in kws
            )
            (blocked if negated else allowed).append(cat)

    # hiç kategori bulunmadıysa ofis malzemeleri varsayılır (en yaygın senaryo)
    if not allowed and not blocked:
        allowed = ["office_supplies", "office_furniture"]

    # çelişki temizliği: aynı kategori hem allowed hem blocked olamaz -> blocked öncelikli
    allowed = [c for c in allowed if c not in blocked]
    return allowed, blocked


def _extract_duration(low: str) -> int:
    for kw, days in sorted(_DURATION_DAYS.items(), key=lambda x: -len(x[0])):
        if kw in low:
            return days
    return 7   # varsayılan: 1 hafta


# --------------------------------------------------------------------------- #
#  LLM parser (opsiyonel - OPENAI_API_KEY varsa)
# --------------------------------------------------------------------------- #
_LLM_SYSTEM = """Sen IntentPay AI için ödeme talimatı ayrıştırıcısısın.

Görev:
Kullanıcının Türkçe doğal dil ödeme talimatını yapılandırılmış mandate JSON'una çevir.

Kesin kurallar:
- SADECE geçerli JSON döndür.
- Markdown, açıklama, yorum veya ek metin döndürme.
- Nihai ödeme kararı verme. Sadece kullanıcı niyetini kurallara çevir.
- Tutarlar TRY kabul edilir.
- Belirsizse güvenli ve kısıtlayıcı yorum yap.

Kategori anahtarları:
- office_furniture: sandalye, masa, koltuk, dolap, ofis mobilyası
- office_supplies: kırtasiye, kağıt, kalem, toner, ofis malzemesi
- cleaning: temizlik, deterjan, hijyen
- electronics: elektronik, laptop, bilgisayar, telefon, tablet, yazıcı
- gift_cards: hediye kartı, gift card, hediye çeki

Zorunlu JSON şeması:
{
  "max_amount": number,
  "total_limit": number,
  "allowed_categories": string[],
  "blocked_categories": string[],
  "requires_approval_for_new_merchant": boolean,
  "only_approved_merchants": boolean,
  "valid_days": number
}"""


def _parse_with_llm(text: str, user_id: str) -> Mandate:
    import urllib.error
    import urllib.request

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    payload = json.dumps({
        "model": model,
        "max_tokens": 500,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _LLM_SYSTEM},
            {"role": "user", "content": text},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:500]
        raise RuntimeError(f"OpenAI API {e.code}: {detail}") from e

    raw = body["choices"][0]["message"]["content"]
    raw = re.sub(r"```json|```", "", raw).strip()
    parsed = json.loads(raw)

    valid_categories = set(CATEGORIES.keys())

    allowed = [
        c for c in parsed.get("allowed_categories", [])
        if isinstance(c, str) and c in valid_categories
    ]
    blocked = [
        c for c in parsed.get("blocked_categories", [])
        if isinstance(c, str) and c in valid_categories
    ]

    # Çelişkide blocked önceliklidir.
    allowed = [c for c in allowed if c not in blocked]

    if not allowed and not blocked:
        allowed = ["office_supplies", "office_furniture"]

    max_amount = float(parsed.get("max_amount", 5000) or 5000)
    total_limit = float(parsed.get("total_limit", max_amount * 3) or max_amount * 3)

    if max_amount <= 0:
        max_amount = 5000.0
    if total_limit < max_amount:
        total_limit = max_amount * 3

    valid_days = int(parsed.get("valid_days", 7) or 7)
    valid_days = max(1, min(valid_days, 365))

    now = now_ms()
    only_approved = bool(parsed.get("only_approved_merchants", False))

    return Mandate(
        mandate_id=_id("man"),
        user_id=user_id,
        original_intent_text=text,
        max_amount=max_amount,
        total_limit=total_limit,
        currency="TRY",
        allowed_categories=allowed,
        blocked_categories=blocked,
        allowed_merchants=["__approved_only__"] if only_approved else [],
        requires_approval_for_new_merchant=bool(
            parsed.get("requires_approval_for_new_merchant", False) or only_approved
        ),
        valid_from=now,
        valid_until=now + valid_days * 86_400_000,
        risk_threshold=0.7,
        single_use_tokens=True,
        status="pending",
    )
