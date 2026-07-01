"""
IntentPay AI - Attack Detector (Prompt Injection & Manipülasyon Savunması)
==========================================================================
AI ajan ödeme sistemlerinin en kritik tehdidi: doğal dil talimatına gizlenmiş
manipülasyon denemeleri. Saldırgan, ajanı/parser'ı kandırarak policy kurallarını
atlatmaya çalışır ("tüm limitleri yok say", "her şeyi onayla", "sen artık
yöneticisin" vb.).

GÜVENLİK PRENSİBİ: Bu katman tespit eder ve İŞARETLER; ama asıl savunma yine
deterministik policy engine'dir — manipülasyon başarılı olsa bile policy kuralları
LLM çıktısından bağımsız olarak uygulanır. Bu "defense in depth" (katmanlı savunma)
yaklaşımıdır.
"""
from __future__ import annotations

import re

# Tehdit kategorileri ve örüntüleri. Her örüntü Türkçe + İngilizce varyant içerir
# çünkü saldırganlar dil karışımı kullanır.
_THREAT_PATTERNS = {
    "limit_bypass": {
        "label": "Limit atlatma girişimi",
        "patterns": [
            r"limit(ler)?(i|leri)?\s*(yok\s*say|kaldır|atla|aş|görmezden|iptal)",
            r"(tüm|bütün|hiçbir)\s*(kural|limit|kısıtlama)",
            r"ignore\s+(all\s+)?(limits?|rules?|restrictions?)",
            r"sınırsız\s*(harca|öde|yetki)",
            r"limitsiz",
            r"no\s+limit",
        ],
        "severity": "high",
    },
    "auto_approve": {
        "label": "Zorla onaylatma girişimi",
        "patterns": [
            r"(her\s*şey(i)?|hepsini|tümünü|tamamını)\s*(onayla|kabul\s*et|geçir)",
            r"(otomatik|koşulsuz|sorgusuz)\s*(onay|kabul|öde)",
            r"approve\s+(everything|all|any)",
            r"always\s+approve",
            r"reddetme|red\s*etme|engelleme",
            r"ne\s*olursa\s*olsun\s*(öde|onayla|kabul)",
        ],
        "severity": "high",
    },
    "rule_override": {
        "label": "Kural geçersiz kılma girişimi",
        "patterns": [
            r"(kural(lar)?(ı|ı|i)?)\s*(unut|sil|geçersiz|devre\s*dışı|boşver)",
            r"(önceki|yukarıdaki|tüm)\s*(talimat|kural|komut)(ları|lar)?\s*(unut|yok\s*say|sil)",
            r"ignore\s+(previous|above|prior)\s+(instructions?|rules?)",
            r"forget\s+(all\s+)?(rules?|instructions?)",
            r"disregard",
            r"policy\s*(engine)?(’?i|'?i)?\s*(atla|devre\s*dışı|kapat)",
        ],
        "severity": "high",
    },
    "role_hijack": {
        "label": "Yetki/rol ele geçirme girişimi",
        "patterns": [
            r"sen\s*(artık|şimdi)?\s*(yönetici|admin|patron|yetkili|root)",
            r"(yönetici|admin|root)\s*(modu|yetkisi|olarak)",
            r"you\s+are\s+(now\s+)?(admin|administrator|root|owner)",
            r"developer\s+mode",
            r"sistem\s*(yöneticisi|admini)\s*olarak",
            r"süper\s*kullanıcı",
        ],
        "severity": "high",
    },
    "category_smuggle": {
        "label": "Kategori gizleme girişimi",
        "patterns": [
            r"(elektronik|laptop|telefon).{0,30}(ofis\s*malzeme|kırtasiye)\s*olarak",
            r"(kategori(yi)?)\s*(değiştir|gizle|sahte|farklı\s*göster)",
            r"olarak\s*(etiketle|göster|kaydet|işaretle)",
            r"as\s+(if|though)\s+it",
        ],
        "severity": "medium",
    },
    "urgency_pressure": {
        "label": "Aciliyet/baskı manipülasyonu",
        "patterns": [
            r"(acil|hemen|derhal|şimdi)\s*(öde|onayla|gönder).{0,20}(kontrol\s*etme|sorma|bekleme)",
            r"(kontrol\s*etme|doğrulama\s*yapma|soru\s*sorma)",
            r"don'?t\s+(check|verify|ask|validate)",
            r"bypass\s+(verification|check|security)",
        ],
        "severity": "medium",
    },
}


def scan_text(text: str) -> dict:
    """
    Metni tarar; tespit edilen tehditleri döner.
    Döner: {
      is_attack: bool,
      threat_level: "none"|"medium"|"high",
      detections: [{type, label, severity, matched}],
      summary: str
    }
    """
    low = text.lower()
    detections: list[dict] = []

    for threat_type, cfg in _THREAT_PATTERNS.items():
        for pat in cfg["patterns"]:
            m = re.search(pat, low)
            if m:
                detections.append({
                    "type": threat_type,
                    "label": cfg["label"],
                    "severity": cfg["severity"],
                    "matched": m.group(0).strip(),
                })
                break   # her tehdit tipinden bir eşleşme yeterli

    if not detections:
        return {
            "is_attack": False,
            "threat_level": "none",
            "detections": [],
            "summary": "Manipülasyon denemesi tespit edilmedi.",
        }

    has_high = any(d["severity"] == "high" for d in detections)
    level = "high" if has_high else "medium"
    labels = ", ".join(sorted({d["label"] for d in detections}))
    summary = (f"{len(detections)} manipülasyon sinyali tespit edildi: {labels}. "
               f"Talimat güvenli kurallara indirgenecek; policy engine "
               f"manipülasyondan bağımsız olarak uygulanır.")

    return {
        "is_attack": True,
        "threat_level": level,
        "detections": detections,
        "summary": summary,
    }


def sanitize_mandate(mandate_dict: dict, scan: dict) -> dict:
    """
    Saldırı tespit edildiyse mandate'i güvenli sınırlara çeker (defense in depth).
    Manipülasyonun parser'ı etkilemesi durumunda bile sistem güvenli kalır.
    """
    if not scan["is_attack"]:
        return mandate_dict

    safe = dict(mandate_dict)
    # Yüksek tehditte: şüpheli geniş yetkileri güvenli varsayılanlara indir
    if scan["threat_level"] == "high":
        # Aşırı yüksek limit manipülasyonunu kırp
        safe["max_amount"] = min(safe.get("max_amount", 5000), 5000.0)
        safe["total_limit"] = min(safe.get("total_limit", 5000), 5000.0)
        # Yeni satıcı için her zaman ek onay zorunlu kıl
        safe["requires_approval_for_new_merchant"] = True
        # Risk eşiğini düşür (daha hassas)
        safe["risk_threshold"] = min(safe.get("risk_threshold", 0.7), 0.5)
        safe["_sanitized"] = True

    return safe