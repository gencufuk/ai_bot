# -*- coding: utf-8 -*-
"""VIP cüzdan döngüsünün karar çekirdeği (I/O yok, saf fonksiyonlar).

ai_bot.py V18.3'teki vip_cuzdan_loop içindeki çıkış mantığı birebir buraya
taşındı; döngü artık sadece veri toplar (fiyat, RSI, hacim), `kararlar()`
fonksiyonunu çağırır ve dönen eylemleri sırayla uygular. Böylece:
  - her dal birim testle sabitlenebilir (tests/test_risk_motoru.py),
  - borsa/Redis hatası karar mantığını bozamaz,
  - strateji değişikliği tek yerden yapılır ve labeler/backtest aynı kodu kullanabilir.

V18.3'e göre BİLİNÇLİ davranış farkları (hepsi bug düzeltmesi):
  1. Yarı satış sonrası taban: V18.3'te m_k < ilk_esik iken `cikis = 0.005`
     kâr kilidini (+%1.0) EZİYORDU (V18.3 kilidi 0.002 -> 0.010 yükseltirken bu
     satır unutulmuş). Artık `max(base_stop, 0.005)`.
  2. Zaman aşımı uzatması giriş zamanını değil ayrı bir referansı (`zaman_ref`)
     günceller; CSV'deki Sure_Saat gerçek süreyi gösterir.
  3. Momentum ölçümü kapanmış mumlarla yapılır (canlı mumun kısmi hacmi
     "hacim söndü" kararını mumun başında sistematik olarak tetikliyordu).
"""
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

# Eylem tipleri
MOON_BAG = 'MOON_BAG'
KISMI_KAR = 'KISMI_KAR'
TAM_CIKIS = 'TAM_CIKIS'
ZAMAN_UZAT = 'ZAMAN_UZAT'
MOMENTUM_KONTROL = 'MOMENTUM_KONTROL'

# Mesajlar: CSV Cikis_Tipi değerleri ve stop sayacı ("STOP" in mesaj) bunlara bağlı, DEĞİŞTİRMEYİN
MSG_MOON = "🚀 MOON BAG (%50 VURKAÇ)"
MSG_KISMI = "DİNAMİK KISMİ KÂR"
MSG_TREND = "📈 TREND TAKİPLİ ÇIKIŞ"
MSG_GUVENLI = "🛡️ GÜVENLİ ÇIKIŞ"
MSG_KILIT = "🔒 KÂR KİLİDİ (+%1)"
MSG_ZAMAN = "⏳ ZAMAN AŞIMI"
MSG_MOMENTUM = "💤 MOMENTUM ÖLDÜ (YATAY REJİM)"


@dataclass(frozen=True)
class RiskAyarlari:
    fee_rate: float = 0.001
    zarar_orani_balina: float = 0.020
    stop_min: float = 0.025          # dinamik stop alt sınırı
    stop_max: float = 0.055          # dinamik stop üst sınırı
    stop_atr_carpan: float = 1.5
    ilk_esik_min: float = 0.03
    ilk_esik_max: float = 0.06
    ilk_esik_atr_carpan: float = 2.0
    kar_kilidi_tetik: float = 0.025  # max kâr bunu geçince stop kilide çekilir
    kar_kilidi_oran: float = 0.010
    yarim_sonrasi_taban: float = 0.005
    max_bekleme_saati: float = 4.0
    min_beklenti_orani: float = 0.005
    momentum_olu_saat: float = 1.5
    momentum_bant: float = 0.01
    momentum_kontrol_aralik_sn: float = 600.0
    moon_bag_min_oran: float = 0.05
    moon_bag_rsi: float = 83.0
    rsi_kontrol_aralik_sn: float = 60.0


@dataclass
class Pozisyon:
    sembol: str
    giris: float                  # gerçekleşen giriş fiyatı
    max_kar: float = 0.0          # görülen en yüksek NET oran
    half_sold: bool = False
    giris_zamani: float = 0.0     # gerçek giriş zamanı (epoch sn) — değişmez
    zaman_ref: Optional[float] = None  # zaman aşımı referansı (uzatmada güncellenir)
    atr_pct: float = 2.5
    is_whale: bool = False
    son_rsi_kontrol: float = 0.0
    son_mom_kontrol: float = 0.0

    @property
    def ref(self) -> float:
        return self.zaman_ref if self.zaman_ref else self.giris_zamani


@dataclass
class Seviyeler:
    oran: float
    max_kar: float
    aktif_zarar: float
    ilk_esik: float
    base_stop: float
    kismi: float
    cikis: float


@dataclass
class Eylem:
    tip: str
    mesaj: str = ''
    oran: float = 1.0             # pozisyonun satılacak kısmı
    detay: dict = field(default_factory=dict)


def net_oran(giris: float, fiyat: float, fee: float) -> float:
    """Alış + satış komisyonu dahil net getiri oranı (ai_bot.net_kar_hesapla ile aynı formül)."""
    return (fiyat * (1 - fee) - giris * (1 + fee)) / (giris * (1 + fee))


def dinamik_stop(atr_pct: float, a: RiskAyarlari = RiskAyarlari()) -> float:
    return max(a.stop_min, min(a.stop_max, (atr_pct * a.stop_atr_carpan) / 100))


def ilk_esik(atr_pct: float, a: RiskAyarlari = RiskAyarlari()) -> float:
    return max(a.ilk_esik_min, min(a.ilk_esik_max, (atr_pct * a.ilk_esik_atr_carpan) / 100))


def aktif_zarar_orani(atr_pct: float, is_whale: bool, a: RiskAyarlari = RiskAyarlari()) -> float:
    return a.zarar_orani_balina if is_whale else dinamik_stop(atr_pct, a)


def seviyeleri_hesapla(p: Pozisyon, fiyat: float, a: RiskAyarlari = RiskAyarlari()) -> Seviyeler:
    oran = net_oran(p.giris, fiyat, a.fee_rate)
    m_k = max(p.max_kar, oran)
    zarar = aktif_zarar_orani(p.atr_pct, p.is_whale, a)
    esik = ilk_esik(p.atr_pct, a)
    base_stop = a.kar_kilidi_oran if m_k >= a.kar_kilidi_tetik else -zarar
    if m_k >= 0.20:
        kismi, cikis = m_k - 0.05, max(base_stop, m_k - 0.10)
    elif m_k >= 0.10:
        kismi, cikis = m_k - 0.02, max(base_stop, m_k - 0.05)
    elif m_k >= esik:
        kismi, cikis = m_k - 0.015, max(base_stop, m_k - 0.03)
    else:
        # V18.3: (0.005 if half_sold else base_stop) -> yarı satıştan sonra kâr kilidini eziyordu
        kismi, cikis = 999.0, (max(base_stop, a.yarim_sonrasi_taban) if p.half_sold else base_stop)
    return Seviyeler(oran, m_k, zarar, esik, base_stop, kismi, cikis)


def rsi_kontrolu_gerekli(p: Pozisyon, sev: Seviyeler, simdi: float, a: RiskAyarlari = RiskAyarlari()) -> bool:
    return (sev.oran >= a.moon_bag_min_oran and not p.half_sold
            and (simdi - p.son_rsi_kontrol) > a.rsi_kontrol_aralik_sn)


def cikis_mesaji(p: Pozisyon, sev: Seviyeler, a: RiskAyarlari = RiskAyarlari()) -> str:
    if sev.max_kar >= sev.ilk_esik:
        return MSG_TREND
    if p.half_sold:
        return MSG_GUVENLI
    if sev.max_kar >= a.kar_kilidi_tetik:
        return MSG_KILIT
    return f"🛑 STOP LOSS (%-{sev.aktif_zarar * 100:.1f})"


def kararlar(p: Pozisyon, sev: Seviyeler, simdi: float, rejim: str,
             a: RiskAyarlari = RiskAyarlari(), rsi_tetik: bool = False) -> List[Eylem]:
    """Uygulanabilir eylemleri V18.3'teki öncelik sırasıyla döndürür.

    Kabuk (ai_bot) listeyi sırayla dener:
      - yarım satış (MOON_BAG/KISMI_KAR) gerçekleşirse sembol için tur biter,
      - tutar min-notional altındaysa bir sonraki eyleme geçilir (V18.3'teki fall-through),
      - yarım satış HATA verirse sadece koruyucu TAM_CIKIS denenir (stop asla atlanmaz).
    En fazla bir "tam çıkış ailesi" eylemi (TAM_CIKIS / ZAMAN_UZAT / MOMENTUM_KONTROL) döner."""
    eylemler: List[Eylem] = []
    if rsi_tetik and not p.half_sold:
        eylemler.append(Eylem(MOON_BAG, MSG_MOON, oran=0.5))
    if sev.max_kar >= sev.ilk_esik and sev.oran <= sev.kismi and not p.half_sold and not rsi_tetik:
        eylemler.append(Eylem(KISMI_KAR, MSG_KISMI, oran=0.5))

    ref_saat = (simdi - p.ref) / 3600
    if sev.oran <= sev.cikis:
        eylemler.append(Eylem(TAM_CIKIS, cikis_mesaji(p, sev, a)))
    elif ref_saat >= a.max_bekleme_saati and not p.half_sold:
        if sev.oran < a.min_beklenti_orani:
            eylemler.append(Eylem(TAM_CIKIS, MSG_ZAMAN))
        else:
            eylemler.append(Eylem(ZAMAN_UZAT))
    elif (rejim == "YATAY" and not p.half_sold and ref_saat >= a.momentum_olu_saat
          and abs(sev.oran) < a.momentum_bant
          and (simdi - p.son_mom_kontrol) > a.momentum_kontrol_aralik_sn):
        eylemler.append(Eylem(MOMENTUM_KONTROL, MSG_MOMENTUM))
    return eylemler


def momentum_oldu_mu(kapali_hacimler: Sequence[float]) -> Optional[bool]:
    """Kapanmış 15m mum hacimleri (eskiden yeniye, canlı mum HARİÇ).
    Son 3 kapalı mumun ortalaması son 14 kapalı mumun ortalamasının altındaysa True.
    Yetersiz veri -> None (karar verilmez)."""
    v = [float(x) for x in kapali_hacimler if x is not None]
    if len(v) < 14:
        return None
    ort = sum(v[-14:]) / 14
    if ort <= 0:
        return None
    return (sum(v[-3:]) / 3) < ort
