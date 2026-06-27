# IntentPay AI — Ödeme Yetkilendirme Katmanı (Hackathon MVP)

> AI ajanlarının kullanıcı niyetine uygun, yetkilendirilmiş ve denetlenebilir
> şekilde ödeme başlatmasını sağlayan güven katmanı.
>
> **Tüm ödeme işlemleri simülasyondur.** Gerçek kart verisi, banka entegrasyonu
> veya canlı ödeme **yoktur**.

---

## Hızlı Başlangıç

```bash
./run.sh
```

Ardından tarayıcıda **http://localhost:8787** adresini açın. Hepsi bu kadar —
backend ve frontend tek sunucudan servis edilir, build adımı yoktur.

> İsteğe bağlı: gerçek LLM ile niyet ayrıştırma için `export ANTHROPIC_API_KEY=...`
> ayarlayın. Ayarlanmazsa sistem otomatik olarak kural tabanlı ayrıştırmaya düşer.

### Manuel başlatma

```bash
cd backend
python train_risk_model.py      # (opsiyonel) XGBoost risk modelini eğitir
python server.py                # http://localhost:8787
```

**Bağımlılıklar:** Çekirdek sistem **saf Python standart kütüphanesiyle** çalışır
(sıfır bağımlılık). Risk modeli için `xgboost`, `numpy`, `scikit-learn`
opsiyoneldir — yoksa eşdeğer deterministik bir fallback devreye girer.

```bash
pip install xgboost numpy scikit-learn   # opsiyonel, daha iyi risk skoru için
```

---

## Uçtan Uca Demo Akışı

1. **Niyet** — Kullanıcı doğal dille talimat yazar
   _("Bu hafta 5.000 TL'ye kadar ofis sandalyesi al, onaylı satıcıdan, elektronik alma")_
2. **Mandate** — Talimat yapılandırılmış kurallara dönüşür, kullanıcı **onaylar**
3. **Ajan** — AI ajan simülatörü bir satın alma senaryosu çalıştırır
4. **Değerlendirme** — Policy Engine → Risk Modeli boru hattı işler
5. **Karar** — Approve / Step-up / Review / Decline + açıklama (+ token)
6. **Denetim** — Tüm karar zinciri audit log ekranında görünür

### Dört Çekirdek Senaryo

| Senaryo | Davranış | Beklenen Karar |
|---|---|---|
| Güvenli İşlem | Limit içi, izinli kategori, güvenilir satıcı | **Approve** + token |
| Limit Aşımı | Tutar limiti/tavanı aşar | **Step-up / Decline** |
| Kategori İhlali | Yasaklı kategori (hediye kartı) | **Decline** |
| Token Tekrar Kullanımı | Kullanılmış token tekrar denenir | **Decline** (replay) |

---

## Mimari — Modüler Bileşenler

```
intentpay/
├── run.sh                     # tek komutla başlatma
├── frontend/
│   ├── index.html             # tasarım sistemi + React (CDN, build yok)
│   └── app.jsx                # tüm demo akışı (React)
└── backend/
    ├── server.py              # HTTP API + statik servis (orkestratör)
    ├── train_risk_model.py    # XGBoost eğitimi
    ├── models/
    │   └── schema.py          # tüm veri modelleri (dataclass)
    ├── data/
    │   ├── synthetic.py       # sentetik veri + marketplace + eğitim verisi
    │   └── risk_model.json    # eğitilmiş XGBoost modeli
    └── services/
        ├── intent_parser.py   # doğal dil → mandate (LLM + kural fallback)
        ├── policy_engine.py   # DETERMİNİSTİK kural kontrolü
        ├── risk_model.py      # XGBoost skor + açıklanabilirlik (+ fallback)
        ├── decision_engine.py # policy + risk → nihai karar + açıklama
        ├── token_sim.py       # tek kullanımlık ödeme yetkisi simülasyonu
        ├── audit_log.py       # denetlenebilir kayıt zinciri
        └── agent_simulator.py # AI ajan + risk senaryoları
```

Her bileşen ayrı bir mantıksal modüldür ve bağımsız test edilebilir.

### Güvenlik Tasarım Prensibi

**LLM/parser ASLA nihai ödeme kararı vermez.** Sadece doğal dili kurallara çevirir
ve çıktı kullanıcı onayından geçer. Nihai karar **deterministik Policy Engine** ve
risk modeli tarafından verilir — aynı girdi her zaman aynı sonucu üretir.

---

## Bileşen Detayları

**Policy Engine** (deterministik) — şu kuralları kontrol eder: mandate durumu &
geçerlilik süresi, ajan yetkisi, token tekrar kullanımı, yasaklı/izinli kategori,
işlem başına tutar limiti, toplam harcama tavanı, satıcı onayı, yeni satıcı step-up.

**Risk Modeli** (XGBoost) — 11 özellikten 0–1 risk skoru üretir; en etkili risk
faktörlerini açıklanabilirlik için döndürür (feature importance). Model yoksa
eşdeğer ağırlıklı deterministik skor kullanılır. Eğitim AUC ≈ 0.78.

**Decision Engine** — policy ihlali → decline; çok yüksek risk → decline; temiz +
düşük risk → approve; uyarı/orta risk → step-up; yüksek risk ama ihlal yok → review.
Her karar için Türkçe açıklama üretir.

**Token Simulation** — onaylanan işlemler için tek kullanımlık, satıcı/tutar/kategori/
süre ile sınırlı yetki üretir. Kullanılan token tekrar denenirse engellenir.

**Audit Log** — her işlemin niyet → mandate → policy → risk → karar → token zincirini
denetlenebilir biçimde kaydeder.

---

## Moka United Entegrasyonu Vizyonu

IntentPay AI; sanal POS, kart üretimi, dijital cüzdan ve fraud altyapılarının
**üzerine** yerleşen bir "AI ödeme güvenliği" modülü olarak konumlanır. Token
simülasyonu gerçek tek-kullanımlık kart/token üretimine, risk modeli mevcut fraud
sistemlerine, policy engine ise kurumsal harcama kontrol kurallarına bağlanabilir.