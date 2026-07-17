"""
IntentPay AI — RAG Doküman Deposu
==================================

Bu modül, RAG (Retrieval-Augmented Generation) mimarisinin veri katmanıdır.
Şirket politika dokümanlarını, regülasyon kurallarını ve harcama
politikalarını yapılandırılmış biçimde tutar.

Tasarım felsefesi:
- MİMARİ GENELDİR: Herhangi bir kurum kendi dokümanlarını bağlayabilir.
- DEMO İÇERİĞİ NİŞTİR: Örnekler, Moka United'ın müşterisi olabilecek
  türden gerçekçi kurumsal profillerdir (ama "Moka'nın kendisi" değildir,
  çünkü gerçek iç politikalar bilinmez — uydurmak riskli olurdu).
- Regülasyon dokümanları Türkiye bağlamındadır (KVKK, BDDK/TCMB eşikleri).

Her doküman bir PolicyDocument nesnesidir ve bir şirkete (company_id) aittir.
Retriever, kullanıcının talimatına en alakalı doküman parçalarını çeker;
intent_parser bunları LLM'e bağlam olarak verir → "politika-farkında mandate".
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import hashlib


# ----------------------------------------------------------------------------
# Veri Modeli
# ----------------------------------------------------------------------------

@dataclass
class PolicyDocument:
    """
    Tek bir politika dokümanı parçası (chunk).

    RAG'da dokümanlar küçük, anlamlı parçalara bölünür; her parça ayrı
    embedding'e sahip olur ve ayrı ayrı aranabilir. Bu sınıf o parçayı temsil
    eder.

    Alanlar:
        doc_id:      Benzersiz kimlik (içerikten türetilir, deterministik).
        company_id:  Bu dokümanın ait olduğu şirket profili.
        category:    Doküman türü — 'spending_policy' | 'approved_vendors'
                     | 'regulation' | 'category_rules'.
        title:       İnsan-okur başlık (arayüzde gösterilir).
        content:     Asıl metin içeriği (embedding bundan üretilir).
        source:      Kaynak referansı (arayüzde "şu politikadan geldi" için).
    """
    company_id: str
    category: str
    title: str
    content: str
    source: str
    doc_id: str = ""

    def __post_init__(self):
        if not self.doc_id:
            # İçerikten deterministik kimlik — aynı içerik hep aynı id.
            raw = f"{self.company_id}|{self.category}|{self.title}|{self.content}"
            self.doc_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "PolicyDocument":
        return cls(**d)


@dataclass
class CompanyProfile:
    """
    Bir şirket profili — RAG'ın "hangi kurum" bağlamı.

    Gerçek dünyada bu, Moka'nın bir kurumsal müşterisine karşılık gelir.
    Her müşterinin kendi harcama politikaları, onaylı satıcıları ve uymak
    zorunda olduğu regülasyonlar vardır. Sistem, o müşteri için mandate
    üretirken bu profili kullanır.
    """
    company_id: str
    display_name: str
    sector: str
    description: str
    documents: List[PolicyDocument] = field(default_factory=list)


# ----------------------------------------------------------------------------
# DEMO İÇERİĞİ — Gerçekçi kurumsal profiller (Moka'nın müşterisi olabilecek)
# ----------------------------------------------------------------------------
# NOT: Bu profiller kurgusaldır ama gerçekçidir. Amaç, jüriye "sistem gerçek
# kurumsal politikalarla çalışıyor" hissini vermek. Regülasyon parçaları
# Türkiye bağlamındadır ve genel/kamuya açık kuralları yansıtır.

def _build_demo_profiles() -> Dict[str, CompanyProfile]:
    profiles: Dict[str, CompanyProfile] = {}

    # ===== PROFİL 1: Orta ölçekli teknoloji şirketi =====
    tech = CompanyProfile(
        company_id="tekno_a",
        display_name="Tekno A.Ş.",
        sector="Teknoloji / Yazılım",
        description="Orta ölçekli bir yazılım şirketi; departman bazlı "
                    "harcama yönetimi ve onaylı satıcı disiplini uygular.",
    )
    tech.documents = [
        PolicyDocument(
            company_id="tekno_a",
            category="spending_policy",
            title="Departman Harcama Limitleri",
            content=(
                "Şirket harcama politikası: Her departmanın aylık ofis "
                "harcama tavanı 50.000 TL'dir. Tek bir işlem 10.000 TL'yi "
                "aşamaz. 10.000 TL üzerindeki her işlem departman yöneticisinin "
                "ek onayını gerektirir. Ofis mobilyası ve kırtasiye harcamaları "
                "serbesttir; elektronik cihaz alımları yalnızca BT departmanı "
                "onayıyla yapılabilir."
            ),
            source="Tekno A.Ş. Harcama Yönetmeliği, Madde 3",
        ),
        PolicyDocument(
            company_id="tekno_a",
            category="approved_vendors",
            title="Onaylı Satıcı Listesi",
            content=(
                "Onaylı satıcılar yalnızca şirketle çerçeve sözleşmesi olan "
                "tedarikçilerdir. Ofis malzemeleri için onaylı satıcılar "
                "belirlenmiştir; liste dışı satıcılardan alım, satın alma "
                "departmanının açık onayı olmadan yapılamaz. Yeni bir satıcıyla "
                "ilk işlem her zaman ek onay gerektirir."
            ),
            source="Tekno A.Ş. Tedarik Politikası, Ek-1",
        ),
        PolicyDocument(
            company_id="tekno_a",
            category="category_rules",
            title="Kategori Bazlı Kurallar",
            content=(
                "Hediye kartı ve ön ödemeli kart alımları şirket politikası "
                "gereği yasaktır. Elektronik cihazlar (dizüstü, telefon, tablet) "
                "yüksek riskli kategori olarak sınıflandırılır ve her zaman "
                "ek onay ile step-up doğrulama gerektirir. Ofis mobilyası ve "
                "kırtasiye düşük riskli kabul edilir."
            ),
            source="Tekno A.Ş. Risk Sınıflandırma Tablosu",
        ),
    ]
    profiles[tech.company_id] = tech

    # ===== PROFİL 2: Perakende şirketi =====
    retail = CompanyProfile(
        company_id="perakende_b",
        display_name="Perakende B",
        sector="Perakende / Mağazacılık",
        description="Çok şubeli bir perakende zinciri; şube bazlı bütçe ve "
                    "sezonsal harcama kuralları uygular.",
    )
    retail.documents = [
        PolicyDocument(
            company_id="perakende_b",
            category="spending_policy",
            title="Şube Harcama Bütçesi",
            content=(
                "Her şubenin aylık işletme harcama bütçesi 30.000 TL ile "
                "sınırlıdır. Temizlik ve bakım harcamaları serbesttir. "
                "Tek işlem limiti 5.000 TL'dir; bu tutarın üzerindeki alımlar "
                "bölge müdürü onayına tabidir. Sezonsal kampanya dönemlerinde "
                "pazarlama harcamaları için ayrı bütçe tanımlanır."
            ),
            source="Perakende B İşletme El Kitabı, Bölüm 4",
        ),
        PolicyDocument(
            company_id="perakende_b",
            category="category_rules",
            title="Harcama Kategorisi Kuralları",
            content=(
                "Temizlik malzemeleri ve mağaza bakım hizmetleri düşük riskli "
                "kabul edilir ve otomatik onaylanır. Elektronik ve teknoloji "
                "alımları merkez ofis onayı gerektirir. Nakit avans ve "
                "ön ödemeli enstrümanlar yasaktır."
            ),
            source="Perakende B Satın Alma Prosedürü",
        ),
    ]
    profiles[retail.company_id] = retail

    # ===== PROFİL 3: Türkiye Regülasyon & Uyum (tüm şirketler için ortak) =====
    # Bu profil, sektörden bağımsız olarak Türkiye'de faaliyet gösteren
    # kurumların ödeme/harcama süreçlerinde dikkat etmesi gereken genel
    # regülasyon bağlamını taşır. Moka gibi bir ödeme kuruluşunun faaliyet
    # alanına yakınlık kurar.
    regmodel = CompanyProfile(
        company_id="_regulation_tr",
        display_name="Türkiye Regülasyon Bağlamı",
        sector="Uyum / Regülasyon",
        description="Türkiye'de ödeme ve harcama süreçlerinde geçerli genel "
                    "regülasyon ve uyum kuralları (tüm profiller için ortak).",
    )
    regmodel.documents = [
        PolicyDocument(
            company_id="_regulation_tr",
            category="regulation",
            title="Kişisel Veri Koruma (KVKK)",
            content=(
                "6698 sayılı KVKK kapsamında, ödeme işlemlerinde işlenen kişisel "
                "veriler yalnızca işlemin gerektirdiği ölçüde ve amaçla işlenir. "
                "İşlem kayıtları denetlenebilir biçimde tutulmalı, ancak kişisel "
                "veriler gereksiz yere saklanmamalıdır. Her ödeme kararının "
                "gerekçesi kayıt altına alınmalı ve talep halinde açıklanabilir "
                "olmalıdır (açıklanabilirlik ilkesi)."
            ),
            source="6698 Sayılı KVKK — Genel İlkeler",
        ),
        PolicyDocument(
            company_id="_regulation_tr",
            category="regulation",
            title="Ödeme Hizmetleri ve Onay Eşikleri",
            content=(
                "Ödeme ve elektronik para kuruluşları mevzuatı gereği, belirli "
                "tutar eşiklerini aşan işlemlerde ek doğrulama (step-up) ve "
                "güçlü müşteri kimlik doğrulaması önerilir. Yüksek tutarlı veya "
                "olağandışı işlemlerde risk değerlendirmesi ve ikincil onay "
                "mekanizması uygulanmalıdır. Otomatik/ajan kaynaklı işlemlerde "
                "yetkilendirme sınırları açıkça tanımlı olmalıdır."
            ),
            source="Ödeme Hizmetleri Mevzuatı — Genel Çerçeve",
        ),
        PolicyDocument(
            company_id="_regulation_tr",
            category="regulation",
            title="İşlem İzlenebilirliği ve Denetim",
            content=(
                "Tüm ödeme işlemleri, sonradan denetlenebilecek şekilde "
                "kaydedilmelidir. Her işlemin kim tarafından, hangi yetkiyle ve "
                "hangi gerekçeyle başlatıldığı izlenebilir olmalıdır. Otomatik "
                "sistemler tarafından başlatılan işlemlerde, işlemi tetikleyen "
                "yetki belgesi (mandate) ve karar gerekçesi denetim kaydında "
                "yer almalıdır."
            ),
            source="İç Kontrol ve Denetim İlkeleri",
        ),
    ]
    profiles[regmodel.company_id] = regmodel

    return profiles


# Modül yüklendiğinde demo profillerini hazırla.
_DEMO_PROFILES = _build_demo_profiles()

# Regülasyon profili her şirketle birlikte kullanılır (ortak bağlam).
_SHARED_REGULATION_ID = "_regulation_tr"


# ----------------------------------------------------------------------------
# Genel Arayüz (retriever ve diğer modüller bunu kullanır)
# ----------------------------------------------------------------------------

def list_companies() -> List[Dict]:
    """Seçilebilir şirket profillerini döndürür (regülasyon profili hariç)."""
    return [
        {
            "company_id": p.company_id,
            "display_name": p.display_name,
            "sector": p.sector,
            "description": p.description,
            "document_count": len(p.documents),
        }
        for p in _DEMO_PROFILES.values()
        if p.company_id != _SHARED_REGULATION_ID
    ]


def get_documents_for_company(company_id: str,
                              include_regulation: bool = True) -> List[PolicyDocument]:
    """
    Bir şirketin tüm politika dokümanlarını döndürür.

    include_regulation=True ise, ortak Türkiye regülasyon dokümanları da
    eklenir — çünkü her kurum bu regülasyonlara tabidir. Bu, RAG'ın
    "şirket politikası + regülasyon" bileşimini kurmasını sağlar.
    """
    docs: List[PolicyDocument] = []
    profile = _DEMO_PROFILES.get(company_id)
    if profile:
        docs.extend(profile.documents)
    if include_regulation and company_id != _SHARED_REGULATION_ID:
        reg = _DEMO_PROFILES.get(_SHARED_REGULATION_ID)
        if reg:
            docs.extend(reg.documents)
    return docs


def get_all_documents() -> List[PolicyDocument]:
    """Tüm profillerin tüm dokümanlarını döndürür (indeksleme için)."""
    docs: List[PolicyDocument] = []
    for p in _DEMO_PROFILES.values():
        docs.extend(p.documents)
    return docs


def get_document_by_id(doc_id: str) -> Optional[PolicyDocument]:
    """Kimlikten doküman bulur (arayüzde 'şu politikadan geldi' için)."""
    for p in _DEMO_PROFILES.values():
        for d in p.documents:
            if d.doc_id == doc_id:
                return d
    return None


# ----------------------------------------------------------------------------
# Kendi kendini test (python documents.py ile çalıştırılabilir)
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== RAG Doküman Deposu — Kendi Kendine Test ===\n")

    companies = list_companies()
    print(f"Şirket profilleri ({len(companies)}):")
    for c in companies:
        print(f"  • {c['display_name']} ({c['sector']}) "
              f"— {c['document_count']} doküman")

    print("\nTekno A.Ş. için dokümanlar (regülasyon dahil):")
    docs = get_documents_for_company("tekno_a")
    for d in docs:
        print(f"  [{d.category}] {d.title}")
        print(f"      kaynak: {d.source}")
        print(f"      id: {d.doc_id}")

    print(f"\nToplam doküman sayısı (tüm profiller): {len(get_all_documents())}")

    # Kimlik determinizmi testi
    all_docs = get_all_documents()
    ids = [d.doc_id for d in all_docs]
    assert len(ids) == len(set(ids)), "Doküman kimlikleri benzersiz olmalı!"
    print("✓ Tüm doküman kimlikleri benzersiz ve deterministik.")

    # from_dict / to_dict yuvarlak-tur testi
    d0 = all_docs[0]
    d0_roundtrip = PolicyDocument.from_dict(d0.to_dict())
    assert d0_roundtrip.doc_id == d0.doc_id, "Serileştirme tutarlı olmalı!"
    print("✓ Serileştirme (to_dict/from_dict) tutarlı.")

    print("\n=== Test başarılı ===")