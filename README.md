# IntentPay AI — Ödeme Yetkilendirme Katmanı (Hackathon MVP)

> AI ajanlarının kullanıcı niyetine uygun, yetkilendirilmiş ve denetlenebilir
> şekilde ödeme başlatmasını sağlayan güven katmanı.
>
> **Tüm ödeme işlemleri simülasyondur.** Gerçek kart verisi, banka entegrasyonu
> veya canlı ödeme **yoktur**.

---

## Kısa Özet

IntentPay AI, AI ajanlarının kullanıcı veya işletme adına ödeme başlatabildiği
senaryolarda güvenli bir yetkilendirme katmanı sağlar. Kullanıcı doğal dille bir
ödeme talimatı verir; sistem bu talimatı yapılandırılmış kurallara dönüştürür,
AI ajanının ödeme denemesini bu kurallara göre kontrol eder, risk skoru üretir
ve işlemi **Approve / Step-up / Review / Decline** kararlarından biriyle sonuçlandırır.

Nihai ödeme kararı LLM tarafından değil, deterministik policy engine ve risk
modeli tarafından verilir. Her karar audit log içinde açıklanabilir şekilde tutulur.

---

## Hızlı Başlangıç

```bash
./run.sh
```

Ardından tarayıcıda şu adresi açın:

```txt
http://localhost:8787
```

Backend ve frontend tek sunucudan servis edilir. Ayrı frontend build adımı yoktur.

FastAPI dokümantasyonu:

```txt
http://localhost:8787/docs
```

---

## Ortam Değişkenleri

Yerel geliştirme için `.env.example` dosyasını `.env` olarak kopyalayın:

```bash
cp .env.example .env
```

Varsayılan `.env` kullanımı SQLite içindir:

```env
PERSISTENCE_BACKEND=sqlite
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay
RESET_DB=0
```

PostgreSQL kullanmak için:

```env
PERSISTENCE_BACKEND=postgres
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay
RESET_DB=0
```

`run.sh`, proje kökünde `.env` dosyası varsa otomatik olarak yükler.

`.env` commitlenmez. Örnek yapı `.env.example` içinde tutulur.

---

## Veritabanı Seçenekleri

IntentPay AI iki persistence backend destekler:

| Backend | Kullanım | Durum |
|---|---|---|
| SQLite | Varsayılan lokal demo veritabanı | Aktif |
| PostgreSQL | Üretim temeline daha yakın kalıcılık adapter'ı | Aktif |

SQLite varsayılan olarak şunu kullanır:

```txt
backend/data/intentpay.db
```

PostgreSQL için lokal Docker örneği:

```bash
docker run --name intentpay-postgres \
  -e POSTGRES_USER=intentpay \
  -e POSTGRES_PASSWORD=intentpay \
  -e POSTGRES_DB=intentpay \
  -p 5432:5432 \
  -d postgres:16
```

Var olan container'ı başlatmak için:

```bash
docker start intentpay-postgres
```

PostgreSQL bağlantısı için `.env`:

```env
PERSISTENCE_BACKEND=postgres
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay
```

Persistence katmanı adapter yapısındadır:

```txt
services/persistence.py              # public facade
services/persistence_sqlite.py       # SQLite adapter
services/persistence_postgres.py     # PostgreSQL adapter
```

Servisler doğrudan SQLite veya PostgreSQL bilmez; `services/persistence.py`
üzerinden çalışır.

---

## Test

Otomatik test paketi:

```bash
bash run.sh test
```

Beklenen sonuç:

```txt
SONUÇ: 21/21 test geçti
```

Testler şu kritik akışları kapsar:

- güvenli işlem onayı ve token üretimi
- HMAC token doğrulaması
- token kurcalama tespiti
- token replay engeli
- limit aşımı
- yasaklı kategori reddi
- step-up onayı ve reddi
- velocity kontrolü
- prompt injection tespiti
- güvenli mandate sanitize akışı
- risk eşiği profilleri
- kalıcılık ve restore
- analytics response alanları

SQLite test için `.env`:

```env
PERSISTENCE_BACKEND=sqlite
```

PostgreSQL test için `.env`:

```env
PERSISTENCE_BACKEND=postgres
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay
```

---

## Uçtan Uca Demo Akışı

1. **Niyet** — Kullanıcı doğal dille ödeme talimatı yazar.
2. **Mandate** — Talimat yapılandırılmış kurallara dönüşür ve kullanıcı tarafından onaylanır.
3. **Ajan** — AI ajan simülatörü satın alma senaryosu çalıştırır.
4. **Değerlendirme** — Policy Engine ve Risk Modeli birlikte çalışır.
5. **Karar** — Sistem approve, step-up, review veya decline kararı üretir.
6. **Token** — Onaylanan işlem için HMAC imzalı tek kullanımlık token üretilir.
7. **Denetim** — Tüm karar zinciri audit log ekranında görünür.

Örnek kullanıcı talimatı:

```txt
Bu hafta 5.000 TL'ye kadar ofis sandalyesi al, onaylı satıcıdan, elektronik alma.
```

---

## Demo Senaryoları

| Senaryo | Davranış | Beklenen Karar |
|---|---|---|
| Güvenli İşlem | Limit içi, izinli kategori, güvenilir satıcı | Approve + token |
| Limit Aşımı | Tutar limiti veya toplam tavan aşılır | Step-up / Decline |
| Kategori İhlali | Yasaklı kategori denenir | Decline |
| Ek Onay Gerekli | Tutar limiti az miktarda aşılır | Step-up |
| Yeni / Riskli Satıcı | Onaysız satıcı veya yeni ajan | Decline / Review |
| Token Tekrar Kullanımı | Kullanılmış token tekrar denenir | Decline |
| Prompt Injection | Kuralları yok saydırmaya çalışan talimat | Block / sanitized mandate |

---

## Güvenlik Özellikleri

### Prompt Injection / Manipülasyon Savunması

`services/attack_detector.py`, talimat içine gizlenmiş manipülasyon denemelerini
yakalar. Örnek riskli kalıplar:

- tüm limitleri yok say
- her şeyi otomatik onayla
- önceki kuralları unut
- sen artık yöneticisin
- kategoriyi farklı göster
- acil işlem, kontrol yapma

Saldırı tespit edilirse sistem fail-closed davranabilir veya mandate'i güvenli
sınırlara çekebilir. Nihai savunma yine deterministik policy engine'dir.

### Deterministik Policy Engine

Policy engine şu kuralları kontrol eder:

- mandate durumu
- geçerlilik süresi
- ajan yetkisi
- token tekrar kullanımı
- yasaklı kategori
- izinli kategori
- işlem başına tutar limiti
- toplam harcama tavanı
- satıcı onayı
- yeni satıcı step-up kontrolü
- velocity limiti

### Risk Modeli

Risk modeli işlem özelliklerinden 0–1 arası risk skoru üretir. Model dosyası yoksa
sistem deterministik fallback risk skorlamasına düşer. Risk profilleri canlı olarak
değiştirilebilir:

- strict
- balanced
- lenient

### HMAC İmzalı Token

Onaylanan işlemler için tek kullanımlık ödeme yetkisi üretilir. Token şu alanlar
üzerinden imzalanır:

- token id
- transaction id
- tutar
- kategori
- satıcı
- geçerlilik süresi

Token kurcalanırsa imza doğrulaması başarısız olur ve redeem reddedilir.

### Velocity Limiti

Kısa sürede çok sayıda işlem bot/fraud sinyali olarak değerlendirilir. Bu sinyal
hem policy engine hem risk modeli tarafında kullanılır.

### Step-up Onay Akışı

Sınırda kalan işlemler doğrudan onaylanmak yerine kullanıcıdan ek onay ister.
Kullanıcı onaylarsa token üretilir; reddederse işlem decline olur.

---

## Analytics ve Audit

Analytics dashboard audit verisinden gerçek zamanlı özet üretir:

- toplam işlem sayısı
- approve / step-up / review / decline dağılımı
- onay oranı
- blok oranı
- ortalama risk skoru
- toplam yetkilendirilen tutar
- en çok tetiklenen policy kuralları
- risk seviye dağılımı
- son işlemler

Audit log her işlem için şu zinciri kaydeder:

```txt
intent → mandate → transaction request → policy evaluation → risk scoring → decision → token
```

Bu yapı kararların sonradan denetlenmesini ve açıklanmasını sağlar.

---

## API

Backend FastAPI üzerinde çalışır.

Temel endpoint'ler:

| Method | Path | Açıklama |
|---|---|---|
| GET | `/api/health` | Sağlık kontrolü |
| GET | `/api/bootstrap` | Demo başlangıç verileri |
| GET | `/api/persistence` | Kalıcılık istatistikleri |
| GET | `/api/audit` | Audit log |
| GET | `/api/tokens` | Token listesi |
| GET | `/api/analytics` | Dashboard verisi |
| POST | `/api/intent/parse` | Doğal dil talimatını mandate'e çevirir |
| POST | `/api/mandate/approve` | Mandate onaylar |
| POST | `/api/agent/request` | Demo ajan işlem isteği üretir |
| POST | `/api/transaction/evaluate` | İşlemi policy + risk ile değerlendirir |
| POST | `/api/security/scan` | Prompt injection taraması yapar |
| POST | `/api/stepup/resolve` | Step-up sonucunu işler |
| POST | `/api/token/tamper` | Token kurcalama simülasyonu yapar |
| POST | `/api/risk/threshold` | Risk tolerans profilini değiştirir |

OpenAPI dokümantasyonu:

```txt
http://localhost:8787/docs
```

---

## Mimari

```txt
IntentPay-AI/
├── README.md
├── run.sh
├── requirements.txt
├── .env.example
├── frontend/
│   ├── index.html
│   └── app.jsx
└── backend/
    ├── server_fastapi.py          # FastAPI HTTP katmanı
    ├── server.py                  # legacy stdlib wrapper
    ├── run_tests.py               # otomatik test paketi
    ├── train_risk_model.py        # risk modeli eğitimi
    ├── models/
    │   └── schema.py              # dataclass modeller
    ├── data/
    │   ├── synthetic.py           # sentetik kullanıcı/ajan/satıcı verisi
    │   └── risk_model.json        # eğitilmiş risk modeli
    └── services/
        ├── app_state.py           # shared AppState / STATE
        ├── api_core.py            # HTTP bağımsız endpoint orchestration
        ├── transaction_service.py # transaction evaluation orchestration
        ├── intent_parser.py       # doğal dil → mandate
        ├── attack_detector.py     # prompt injection savunması
        ├── policy_engine.py       # deterministik policy kuralları
        ├── risk_model.py          # risk skoru + fallback
        ├── decision_engine.py     # policy + risk → nihai karar
        ├── token_sim.py           # HMAC token simülasyonu
        ├── audit_log.py           # audit zinciri
        ├── persistence.py         # persistence facade
        ├── persistence_sqlite.py  # SQLite adapter
        ├── persistence_postgres.py# PostgreSQL adapter
        └── agent_simulator.py     # demo ajan senaryoları
```

### Katman Sorumlulukları

| Katman | Sorumluluk |
|---|---|
| `server_fastapi.py` | HTTP route, Pydantic request schema, CORS, static frontend |
| `api_core.py` | HTTP bağımsız endpoint orchestration |
| `transaction_service.py` | policy, risk, decision, token, audit işlem akışı |
| `app_state.py` | runtime state ve restore mantığı |
| `persistence.py` | SQLite/PostgreSQL adapter seçimi |
| `server.py` | legacy stdlib compatibility wrapper |

---

## Tasarım Prensipleri

### LLM nihai karar vermez

LLM veya kural tabanlı parser yalnızca doğal dili yapılandırılmış mandate'e çevirir.
Nihai ödeme kararı deterministik policy engine ve risk modeli tarafından verilir.

### Fail-closed yaklaşım

Belirsiz, saldırgan veya kural dışı işlemler varsayılan olarak güvenli tarafa çekilir:
step-up, review veya decline.

### Açıklanabilirlik

Her karar; policy sonucu, risk skoru, risk faktörleri ve açıklama metniyle birlikte
kaydedilir.

### Adapter tabanlı kalıcılık

Persistence facade sayesinde SQLite ve PostgreSQL aynı public API ile çalışır.
Bu yapı PostgreSQL geçişini endpoint ve business logic katmanlarından izole eder.

---

## Manuel Komutlar

SQLite ile başlatma:

```bash
PERSISTENCE_BACKEND=sqlite bash run.sh fresh
```

PostgreSQL ile başlatma:

```bash
PERSISTENCE_BACKEND=postgres \
DATABASE_URL=postgresql://intentpay:intentpay@localhost:5432/intentpay \
bash run.sh fresh
```

Test:

```bash
bash run.sh test
```

Risk modelini yeniden eğitme:

```bash
cd backend
python train_risk_model.py
```

---

## Bağımlılıklar

Ana backend bağımlılıkları `requirements.txt` içindedir.

Temel HTTP katmanı:

- FastAPI
- Uvicorn

PostgreSQL adapter:

- psycopg[binary]

Risk modeli için opsiyonel paketler kullanılabilir:

- xgboost
- numpy
- scikit-learn

Risk modeli bağımlılıkları yoksa sistem deterministik fallback risk skoru ile çalışır.

---

## Moka United Entegrasyonu Vizyonu

IntentPay AI; sanal POS, kart üretimi, dijital cüzdan ve fraud altyapılarının
üzerine yerleşen bir AI ödeme güvenliği modülü olarak konumlanır.

Olası entegrasyon noktaları:

- token simülasyonu → gerçek tek kullanımlık kart/token üretimi
- policy engine → kurumsal harcama kontrol kuralları
- risk modeli → mevcut fraud sistemleri
- audit log → uyum, denetim ve itiraz süreçleri
- step-up akışı → kullanıcıdan ek onay alma mekanizması

---

## Mevcut Durum

Bu branch kapsamında tamamlanan ana geliştirmeler:

- FastAPI migration
- Pydantic request schema hardening
- OpenAPI `/docs` desteği
- HTTP bağımsız `api_core.py`
- transaction evaluation service extraction
- shared `AppState` extraction
- legacy `server.py` wrapper dönüşümü
- SQLite persistence adapter extraction
- PostgreSQL persistence adapter
- `.env` / `.env.example` ortam yönetimi
- realtime analytics dashboard
- audit explainability view
- guided demo transaction scenarios
- 21/21 otomatik test geçişi

---

## Not

Bu proje hackathon MVP/prototipidir. Gerçek ödeme, gerçek kart saklama, canlı banka
entegrasyonu veya üretim ortamı güvenlik sertifikasyonu içermez. Amaç, AI ajan
ödemeleri için güvenli yetkilendirme, karar açıklanabilirliği ve denetlenebilirlik
katmanını göstermektir.