# -*- coding: utf-8 -*-
"""Tek tip, çıkış-politikasından bağımsız etiket (core + shadow + backfill ortak).

Neden: Core işlemlerin etiketi (gerçekleşen Net_Kar_USDT) botun o anki çıkış
mantığına (trailing, moon bag, kâr kilidi sürümü...) ve kasa büyüklüğüne bağlı;
shadow etiketi ise sabit +%4/-%3 bracket'ti ve komisyonsuzdu. İki farklı etiket
fonksiyonuyla etiketlenmiş iki veri seti birleştirilirse model "trade kalitesini"
değil "hangi etiketleyiciden geldiğini" öğrenir (REJIM_YATAY shadow'ları ile TREND
core'ları BTC_ADX ekseninde ayrıştığı için BTC_ADX bu sızıntının taşıyıcısı olur).

Bu modül HER sinyali aynı şekilde etiketler:
  - Bariyerler botun kendi risk parametrelerinden türetilir (risk_motoru):
      alt  = -aktif_zarar_orani(ATR, balina)   (%2.5-%5.5 / balina %2)
      üst  = +ilk_esik(ATR)                    (%3-%6, ilk kâr kademesi)
      kâr kilidi: max net kâr %2.5'i görünce alt bariyer +%1'e çekilir
      zaman: 4 saat (MAX_BEKLEME_SAATI)
  - Tüm getiriler NET (alış+satış komisyonu dahil).
  - Aynı mumda iki bariyer de görülürse muhafazakâr: alt bariyer.
  - Mum bariyerin ötesinde açılırsa (gap) çıkış açılış fiyatından hesaplanır.
Etiket sürümü (`ETIKET_SURUMU`) değişirse geçmiş sinyaller yeniden etiketlenebilir
(shadow_labeler.py --yeniden); tüm girdiler OHLCV'den geldiği için bu ucuzdur.
"""
from dataclasses import dataclass
from typing import Optional, Sequence

from .risk_motoru import RiskAyarlari, aktif_zarar_orani, ilk_esik, net_oran

ETIKET_SURUMU = 'ub-v1'


@dataclass(frozen=True)
class EtiketAyarlari:
    pencere_saat: float = 4.0
    kilit_aktif: bool = True
    giris_kaymasi: float = 0.0005   # shadow/backfill: sinyal fiyatı -> gerçekçi dolum (market alım kayması)


def bariyerler(atr_pct: float, is_whale: bool, risk: RiskAyarlari = RiskAyarlari()):
    """(alt_bariyer_orani, ust_bariyer_orani) — ikisi de pozitif büyüklük."""
    return aktif_zarar_orani(atr_pct, is_whale, risk), ilk_esik(atr_pct, risk)


def simule_et(giris: float, mumlar: Sequence[Sequence[float]], sl: float, tp: float,
              risk: RiskAyarlari = RiskAyarlari(), ayar: EtiketAyarlari = EtiketAyarlari(),
              mum_suresi_dk: float = 1.0) -> Optional[dict]:
    """mumlar: [[ts, o, h, l, c, v], ...] sinyal anından itibaren kronolojik.
    Dönen sözlük: sonuc (TP/SL/KILIT/ZAMAN), getiri (net oran), max_net, min_net, mum (çıkış mum indeksi)."""
    if not mumlar or giris <= 0:
        return None
    fee = risk.fee_rate
    max_mum = max(1, int(round(ayar.pencere_saat * 60 / mum_suresi_dk)))
    pencere = list(mumlar[:max_mum])
    alt = -sl
    kilitli = False
    max_net = min_net = net_oran(giris, giris, fee)
    for i, (_ts, o, h, l, c, _v) in enumerate(pencere):
        o, h, l, c = float(o), float(h), float(l), float(c)
        n_o, n_h, n_l = net_oran(giris, o, fee), net_oran(giris, h, fee), net_oran(giris, l, fee)
        if n_o <= alt:          # bariyerin altında açıldı (gap): açılıştan çık
            return _sonuc('KILIT' if kilitli else 'SL', n_o, max(max_net, n_o), min(min_net, n_o), i)
        if n_o >= tp:
            return _sonuc('TP', n_o, max(max_net, n_o), min(min_net, n_o), i)
        min_net = min(min_net, n_l)
        if n_l <= alt:          # muhafazakâr: aynı mumda üst de görüldüyse bile önce alt
            return _sonuc('KILIT' if kilitli else 'SL', alt, max(max_net, min(n_h, tp)), min_net, i)
        max_net = max(max_net, n_h)
        if n_h >= tp:
            return _sonuc('TP', tp, max_net, min_net, i)
        if ayar.kilit_aktif and not kilitli and max_net >= risk.kar_kilidi_tetik:
            kilitli = True
            alt = max(alt, risk.kar_kilidi_oran)
            # Kilidi tetikleyen tepe bu mumda; kapanış zaman olarak tepeden SONRA gelir.
            # Kapanış kilit seviyesinin altındaysa fiyat tepeden sonra kilidi aşağı kesmiştir.
            if net_oran(giris, c, fee) <= alt:
                return _sonuc('KILIT', alt, max_net, min_net, i)
    if len(pencere) < max_mum:
        return None             # pencere henüz dolmadı / veri eksik
    son = net_oran(giris, float(pencere[-1][4]), fee)
    return _sonuc('ZAMAN', son, max_net, min_net, len(pencere) - 1)


def _sonuc(tip, getiri, max_net, min_net, i):
    return {'sonuc': tip, 'getiri': float(getiri), 'max_net': float(max_net),
            'min_net': float(min_net), 'mum': int(i), 'surum': ETIKET_SURUMU}


def sinyali_etiketle(fiyat: float, atr_pct: float, is_whale: bool, mumlar, *,
                     kayma_uygula: bool = True, risk: RiskAyarlari = RiskAyarlari(),
                     ayar: EtiketAyarlari = EtiketAyarlari(), mum_suresi_dk: float = 1.0) -> Optional[dict]:
    """Sinyal fiyatından (shadow/backfill: kayma eklenir; core: gerçek dolum, kayma yok) etiketle."""
    giris = fiyat * (1 + ayar.giris_kaymasi) if kayma_uygula else fiyat
    sl, tp = bariyerler(atr_pct, is_whale, risk)
    s = simule_et(giris, mumlar, sl, tp, risk, ayar, mum_suresi_dk)
    if s is not None:
        s.update({'sl': sl, 'tp': tp, 'giris': giris})
    return s
