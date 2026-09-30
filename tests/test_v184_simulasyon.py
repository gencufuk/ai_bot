# -*- coding: utf-8 -*-
"""tools/v184_simulasyon.py: çıkış motoru, bütçe kuralları, mum deposu ve uçtan uca çalıştırma."""
import argparse
import json
import os
import sys
from dataclasses import replace

import ccxt
import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
import v184_simulasyon as vs  # noqa: E402
from sniper import risk_motoru as risk  # noqa: E402

T0 = 1_786_000_000_000 - (1_786_000_000_000 % 3_600_000)
FEE = vs.RISK_V184.fee_rate
TREND = (lambda t: 'TREND')
YATAY = (lambda t: 'YATAY')


def esik_fiyati(giris, oran):
    return giris * (1 + FEE) * (1 + oran) / (1 - FEE)


def mum(dk, o, h, l, c, v=1000.0):
    return [T0 + dk * 60_000, o, h, l, c, v]


def duz_m15(n=120, fiyat=100.0, v=1000.0, bitis=T0 + 60_000):
    """Girişten önce biten n adet düz 15m mum (RSI tanımsız/50 civarı -> moon bag tetiklenmez)."""
    bas = bitis - n * 900_000
    return np.array([[bas + i * 900_000, fiyat, fiyat * 1.001, fiyat * 0.999, fiyat * (1 + 0.0001 * (-1) ** i), v]
                     for i in range(n)])


def motor(**k):
    varsayilan = dict(kayma_seviye=0.0015, kayma_zaman=0.0005, max_saat=96.0)
    varsayilan.update(k)
    ayar = varsayilan.pop('ayar', vs.RISK_V184)
    return vs.CikisMotoru(ayar, **varsayilan)


GIRIS_MS = T0 + 30_000          # dakikanın ortasında giriş


def test_risk_ayarlari_canli_botla_ayni():
    for k in ('BINANCE_API_KEY', 'BINANCE_SECRET_KEY', 'TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID'):
        os.environ.setdefault(k, 'test')
    import ai_bot
    assert vs.RISK_V184 == ai_bot.RISK_AYAR
    assert vs.MAX_ATR_V184 == pytest.approx(ai_bot.MAX_ATR_PCT * 100)
    assert vs.bf.ADX_TREND_ESIK == ai_bot.ADX_TREND_ESIK and vs.bf.BTC_HISTEREZIS == ai_bot.BTC_HISTEREZIS
    assert vs.RISK_ESKI.kar_kilidi_oran == 0.002 and vs.RISK_ESKI.momentum_olu_saat > 1e6


def test_stop_tam_esik_fiyatinda_kayma_ile():
    m1 = [mum(0, 100, 100, 100, 100),                    # giriş dakikası: yalnız kapanış
          mum(1, 100, 100.2, 97.0, 97.2)]                 # kırmızı: o -> h -> l -> c
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    stop = esik_fiyati(100.0, -0.025)
    assert s.durum == 'KAPANDI' and len(s.bacaklar) == 1
    t, pay, dolum, mesaj = s.bacaklar[0]
    assert mesaj == '🛑 STOP LOSS (%-2.5)' and pay == 1.0
    assert dolum == pytest.approx(stop * (1 - 0.0015), rel=1e-9)
    # kesişim anı tepe (20. sn) ile dip (40. sn) arasında doğrusal
    beklenen_t = T0 + 60_000 + 20_000 + 20_000 * (100.2 - stop) / (100.2 - 97.0)
    assert abs(t - beklenen_t) <= 1
    assert s.getiri == pytest.approx(risk.net_oran(100.0, dolum, FEE))


def test_giris_dakikasinin_giris_oncesi_fitili_stop_tetiklemez():
    m1 = [mum(0, 100, 100.1, 90.0, 100),                  # giriş öncesi dip -%10 (giriş 30. sn'de)
          mum(1, 100, 100.3, 99.8, 100.1)]
    s = motor(max_saat=0.05).simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert s.durum in ('SINIR', 'ACIK') and 'STOP' not in s.cikislar


def test_bosluklu_acilis_acilistan_cikar():
    m1 = [mum(0, 100, 100, 100, 100), mum(1, 96.0, 96.2, 95.5, 95.8)]
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    t, _, dolum, mesaj = s.bacaklar[0]
    assert 'STOP' in mesaj and t == T0 + 60_000
    assert dolum == pytest.approx(96.0 * (1 - 0.0005))       # eşikten değil açılıştan; zaman kayması


@pytest.mark.parametrize('ayar,kilit', [(vs.RISK_V184, 0.010), (vs.RISK_ESKI, 0.002)])
def test_kar_kilidi(ayar, kilit):
    # max net kâr ~%2.8 (kilit tetik %2.5, ilk kâr eşiği %3'ün altında) sonra düşüş
    tepe = esik_fiyati(100.0, 0.028)
    m1 = [mum(0, 100, 100, 100, 100), mum(1, 100, tepe, 100, tepe * 0.999), mum(2, tepe * 0.999, tepe, 99.0, 99.2)]
    s = motor(ayar=ayar).simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert s.son_mesaj == risk.MSG_KILIT and len(s.bacaklar) == 1
    assert s.bacaklar[0][2] == pytest.approx(esik_fiyati(100.0, kilit) * (1 - 0.0015), rel=1e-9)


def test_kismi_kar_sonra_trend_cikisi():
    # m_k = %4.5: ilk kâr eşiği (%3) üstünde, moon bag RSI kontrolünün (%5) altında
    tepe = esik_fiyati(100.0, 0.045)
    m1 = [mum(0, 100, 100, 100, 100), mum(1, 100, tepe, 100, tepe), mum(2, tepe, tepe, 97.0, 97.5)]
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert [b[3] for b in s.bacaklar] == [risk.MSG_KISMI, risk.MSG_TREND]
    assert [b[1] for b in s.bacaklar] == [0.5, 0.5]
    assert s.bacaklar[0][2] == pytest.approx(esik_fiyati(100.0, 0.030) * (1 - 0.0015), rel=1e-9)   # m_k - %1.5
    assert s.bacaklar[1][2] == pytest.approx(esik_fiyati(100.0, 0.015) * (1 - 0.0015), rel=1e-9)   # m_k - %3
    assert s.bacaklar[0][0] < s.bacaklar[1][0]


def _duz_seri(dk, fiyat, bas_dk=1):
    return [mum(bas_dk + i, fiyat, fiyat * 1.0002, fiyat * 0.9998, fiyat) for i in range(dk)]


def test_zaman_asimi_4_saatte():
    m1 = [mum(0, 100, 100, 100, 100)] + _duz_seri(300, 100.1)
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    t, _, dolum, mesaj = s.bacaklar[0]
    assert mesaj == risk.MSG_ZAMAN
    assert GIRIS_MS + 4 * 3_600_000 <= t < GIRIS_MS + 4 * 3_600_000 + 60_000
    assert 100.1 * 0.9998 * 0.9995 <= dolum <= 100.1 * 1.0002 * 0.9995     # o anki yol noktası - zaman kayması


def test_zaman_uzatmasi_karda_devam_eder():
    fiyat = esik_fiyati(100.0, 0.012)                      # net +%1.2 >= %0.5 beklenti -> uzat
    m1 = [mum(0, 100, 100, 100, 100)] + _duz_seri(300, fiyat) + [mum(301, fiyat, fiyat, 96.0, 96.5)]
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert s.son_mesaj.startswith('🛑 STOP') and s.cikis_ms > GIRIS_MS + 5 * 3_600_000


def _m15_hacim(hacimler, fiyat=100.0, bitis=T0 + 3 * 3_600_000):
    n = len(hacimler)
    bas = bitis - n * 900_000
    return np.array([[bas + i * 900_000, fiyat, fiyat, fiyat, fiyat, v] for i, v in enumerate(hacimler)])


def test_momentum_cikisi_yalniz_yatay_rejimde():
    m1 = [mum(0, 100, 100, 100, 100)] + _duz_seri(200, 100.3)
    m15 = _m15_hacim([1000.0] * 20 + [100.0] * 12)          # hacim söndü
    yatay = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, m15, YATAY)
    assert yatay.son_mesaj == risk.MSG_MOMENTUM
    assert yatay.cikis_ms >= GIRIS_MS + 1.5 * 3_600_000
    trend = motor(max_saat=3.0).simule_et(100.0, GIRIS_MS, 1.0, False, m1, m15, TREND)
    assert risk.MSG_MOMENTUM not in trend.cikislar
    # hacim canlıysa yatayda da çıkmaz
    canli = motor(max_saat=3.0).simule_et(100.0, GIRIS_MS, 1.0, False, m1, _m15_hacim([1000.0] * 32), YATAY)
    assert risk.MSG_MOMENTUM not in canli.cikislar


def test_moon_bag_rsi_ile_yarim_satis():
    yukselen = np.array([[T0 + 60_000 - (100 - i) * 900_000, 50 + i * 0.5, 50 + i * 0.5 + 0.3, 50 + i * 0.5 - 0.1,
                          50 + i * 0.5 + 0.25, 1000] for i in range(100)])      # sürekli yükselen: RSI ~100
    fiyat = esik_fiyati(100.0, 0.055)                                          # net >= %5 (moon bag koşulu)
    m1 = [mum(0, 100, 100, 100, 100), mum(1, 100, fiyat, 100, fiyat)] + _duz_seri(3, fiyat, bas_dk=2)
    s = motor(max_saat=0.1).simule_et(100.0, GIRIS_MS, 2.5, False, m1, yukselen, TREND)
    assert s.bacaklar[0][3] == risk.MSG_MOON and s.bacaklar[0][1] == 0.5
    assert s.bacaklar[0][2] == pytest.approx(fiyat * (1 - 0.0005))


def test_rsi_onbellegi_sonucu_degistirmez():
    """RSI canlı fiyatta monoton: önbellekli ve önbelleksiz motor her yolda aynı kararı vermeli
    (moon bag tetiklenen ve tetiklenmeyen yollar birlikte)."""
    moon = kismi = 0
    for tohum in range(16):
        rng = np.random.default_rng(tohum)
        m1 = _rastgele_m1(100 + tohum, n=240)
        for r in m1[1:]:                                      # net %5 üstü: RSI kontrolü her dakika
            r[1:5] = [x * 1.055 for x in r[1:5]]
        kap = np.exp(np.cumsum(rng.normal(-0.001 + 0.001 * (tohum % 4), 0.006, 140)))
        kap = kap / kap[-1] * 100                             # 15m geçmiş giriş fiyatında biter
        m15 = np.array([[T0 + 60_000 - (140 - i) * 900_000, k, k * 1.01, k * 0.99, k, 1000] for i, k in enumerate(kap)])
        sonuc = []
        for onbellek in (True, False):
            mt = motor(max_saat=3.9)
            mt.rsi_onbellek = onbellek
            sonuc.append(mt.simule_et(100.0, GIRIS_MS, 2.5, False, m1, m15, TREND))
        assert sonuc[0].bacaklar == sonuc[1].bacaklar, tohum
        moon += risk.MSG_MOON in sonuc[0].cikislar
        kismi += risk.MSG_KISMI in sonuc[0].cikislar
    assert moon >= 3 and kismi >= 3


def test_bosluk_acilisinda_kismi_sonra_kalan_ayni_fiyattan():
    """Yarım satıştan sonraki tur (canlıda 2 sn) eşiğin altındaki kalan yarıyı hemen satar."""
    tepe = esik_fiyati(100.0, 0.045)                     # m_k %4.5 -> kısmi %3, çıkış %1.5
    dip = esik_fiyati(100.0, 0.005)                      # mum ikisinin de altında açılıyor
    m1 = [mum(0, 100, 100, 100, 100), mum(1, 100, tepe, 100, tepe), mum(2, dip, dip, dip, dip)]
    s = motor().simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert [b[3] for b in s.bacaklar] == [risk.MSG_KISMI, risk.MSG_TREND]
    assert s.bacaklar[1][0] - s.bacaklar[0][0] == 2_000              # sonraki tur, 2 sn
    assert s.bacaklar[0][2] == s.bacaklar[1][2] == pytest.approx(dip * (1 - 0.0005))


def test_sinir_suresinde_kapatir():
    m1 = [mum(0, 100, 100, 100, 100)] + _duz_seri(120, esik_fiyati(100.0, 0.012))
    s = motor(max_saat=1.0).simule_et(100.0, GIRIS_MS, 1.0, False, m1, duz_m15(), TREND)
    assert s.durum == 'SINIR' and s.son_mesaj == 'SIM_SINIR'


# --- yoğun yol ile diferansiyel test -------------------------------------------------------
class YogunMotor(vs.CikisMotoru):
    """Her bacağı 200 noktaya böler ve eşik kesişimi eklemez: canlı botun sürekli taramasına yakınsar."""
    kesisim_ekle = False

    def _noktalar(self, m1, giris_ms):
        onceki = None
        for f, t, surekli in vs.CikisMotoru._noktalar(m1, giris_ms):
            if surekli and onceki is not None:
                for q in np.linspace(0, 1, 201)[1:-1]:
                    yield onceki[0] + (f - onceki[0]) * q, int(onceki[1] + (t - onceki[1]) * q), False
            yield f, t, False
            onceki = (f, t)


def _rastgele_m1(tohum, n=180):
    rng = np.random.default_rng(tohum)
    r = rng.normal(0.0002, 0.004, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.002, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.002, n)))
    return [[T0 + i * 60_000, o[i], h[i], l[i], c[i], 1000.0] for i in range(n)]


def test_olay_tabanli_motor_yogun_yolla_ayni_karari_verir():
    ayar = replace(vs.RISK_V184, moon_bag_rsi=101.0)       # zaman/RSI tetikleri kapalı: yalnız fiyat eşikleri
    uyumsuz, karar = [], 0
    for tohum in range(80):
        m1 = _rastgele_m1(tohum)
        atr = 0.5 + (tohum % 8) * 0.5
        balina = tohum % 5 == 0
        a = vs.CikisMotoru(ayar, 0.0, 0.0, max_saat=2.9).simule_et(100.0, GIRIS_MS, atr, balina, m1, duz_m15(), TREND)
        b = YogunMotor(ayar, 0.0, 0.0, max_saat=2.9).simule_et(100.0, GIRIS_MS, atr, balina, m1, duz_m15(), TREND)
        # SIM_SINIR yapay kapanıştır (süre sınırı), karar değil: yalnız motorun verdiği kararlar karşılaştırılır
        ka = [x for x in a.bacaklar if not x[3].startswith('SIM_')]
        kb = [x for x in b.bacaklar if not x[3].startswith('SIM_')]
        if [x[3] for x in ka] != [x[3] for x in kb] or any(
                abs(x[2] / y[2] - 1) > 2e-4 or x[1] != y[1] or abs(x[0] - y[0]) > 1_000 for x, y in zip(ka, kb)):
            uyumsuz.append((tohum, a.cikislar, b.cikislar, [x[2] for x in ka], [y[2] for y in kb]))
        karar += bool(ka)
    assert karar >= 40                                     # örneklerin çoğu gerçekten eşik kararı içeriyor
    assert not uyumsuz, uyumsuz[:3]


# --- bütçe kuralları -----------------------------------------------------------------------
def _islem(s, g_dk, bacaklar, mesaj='📈 TREND TAKİPLİ ÇIKIŞ', kasa='NORMAL'):
    return {'sembol': s, 'giris_ms': T0 + g_dk * 60_000, 'kasa_tipi': kasa,
            'bacaklar': [(T0 + t * 60_000, pay, g) for t, pay, g in bacaklar], 'son_mesaj': mesaj}


def test_ayni_coin_cooldown_ve_kara_liste():
    islemler = [
        _islem('A', 0, [(30, 1.0, 0.01)]),
        _islem('A', 10, [(40, 1.0, 0.01)]),                 # A açıkken: atlanır
        _islem('A', 60, [(70, 1.0, 0.01)]),                 # çıkıştan 30 dk sonra: cooldown
        _islem('A', 100, [(110, 1.0, -0.03)], '🛑 STOP LOSS (%-2.5)'),
        _islem('A', 200, [(210, 1.0, -0.03)], '🛑 STOP LOSS (%-2.5)'),   # 2. stop -> 24 saat kara liste
        _islem('A', 300, [(310, 1.0, 0.02)]),               # kara listede
        _islem('A', 300 + 24 * 60 + 5, [(24 * 60 + 400, 1.0, 0.02)]),   # 24 saat sonra serbest
    ]
    df, _, atlanan, acik = vs.oynat(islemler, 1000, 'sabit')
    assert atlanan == {'acik_pozisyon': 1, 'cooldown': 1, 'kara_liste': 1}
    assert len(df) == 4 and acik == 0


def test_kismi_satisin_parasi_aninda_kasaya_doner():
    islemler = [_islem('A', 0, [(10, 0.5, 0.05), (100, 0.5, 0.02)]), _islem('B', 20, [(50, 1.0, 0.01)])]
    df, _, atlanan, _ = vs.oynat(islemler, 30, 'sabit')
    # A açılınca nakit 10; 10. dk'da yarısı %5 kârla döner -> 20.5 >= 20 x 1.01 -> B alınır
    assert not atlanan and len(df) == 2
    df2, _, atlanan2, _ = vs.oynat([_islem('A', 0, [(100, 1.0, 0.035)]), islemler[1]], 30, 'sabit')
    assert atlanan2 == {'bakiye': 1}


def test_kara_liste_yalniz_pes_pese_stoplarda():
    """Botla aynı kural: arada stop dışı (ör. kâr kilidi) çıkış varsa iki stop kara liste sayılmaz."""
    islemler = [
        _islem('A', 0, [(10, 1.0, -0.03)], '🛑 STOP LOSS (%-2.5)'),
        _islem('A', 100, [(110, 1.0, 0.01)], '🔒 KÂR KİLİDİ (+%1)'),
        _islem('A', 200, [(210, 1.0, -0.03)], '🛑 STOP LOSS (%-2.5)'),
        _islem('A', 300, [(310, 1.0, 0.02)]),               # kara listede değil: alınır
    ]
    df, _, atlanan, _ = vs.oynat(islemler, 1000, 'sabit')
    assert len(df) == 4 and not atlanan


def test_oransal_mod_ozsermaye_payi():
    islemler = [_islem('A', 0, [(10, 1.0, 0.10)]), _islem('B', 20, [(30, 1.0, 0.0)], kasa='BALİNA')]
    df, egri, _, _ = vs.oynat(islemler, 100, 'oransal', 0.2)
    assert df['boyut'].tolist() == pytest.approx([20.0, 2 * 0.2 * 102.0])
    assert float(egri.iloc[-1]) == pytest.approx(102.0)


# --- mum deposu -----------------------------------------------------------------------------
class SayanBorsa:
    def __init__(self, veri):
        self.veri, self.istekler = veri, []

    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        self.istekler.append((sym, tf, since))
        if (sym, tf) not in self.veri:
            raise ccxt.BadSymbol(f'{sym} yok')
        d = self.veri[(sym, tf)]
        d = d[d[:, 0] >= since][:limit]
        return d.tolist()


def _m1_dizi(n, bas=T0, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    return np.array([[bas + i * 60_000, c[i], c[i] * 1.001, c[i] * 0.999, c[i], 10.0] for i in range(n)])


def test_mum_deposu_onbellek_ve_acik_mum(tmp_path):
    veri = {('A/USDT', '1m'): _m1_dizi(5000)}
    simdi = T0 + 3000 * 60_000 + 30_000                    # 3000. dakikanın ortası: o mum henüz açık
    ex = SayanBorsa(veri)
    d = vs.MumDeposu(ex, str(tmp_path), simdi_ms=simdi)
    a = d.getir('A/USDT', '1m', T0, T0 + 1500 * 60_000)
    assert len(a) == 1500 and len(ex.istekler) == 2
    assert len(d.getir('A/USDT', '1m', T0 + 100 * 60_000, T0 + 1200 * 60_000)) == 1100 and len(ex.istekler) == 2
    b = d.getir('A/USDT', '1m', T0 + 1000 * 60_000, T0 + 4000 * 60_000)
    assert b[-1, 0] == T0 + 2999 * 60_000                  # açık mum yok
    n_istek = len(ex.istekler)
    ex2 = SayanBorsa(veri)
    d2 = vs.MumDeposu(ex2, str(tmp_path), simdi_ms=simdi)   # diskten
    assert len(d2.getir('A/USDT', '1m', T0, T0 + 2500 * 60_000)) == 2500 and not ex2.istekler
    assert n_istek >= 3
    with pytest.raises(vs.VeriYok):
        d.getir('YOK/USDT', '1m', T0, T0 + 60_000)
    with pytest.raises(vs.VeriYok):                        # ikinci kez sorulmaz
        d.getir('YOK/USDT', '15m', T0, T0 + 60_000)
    assert sum(1 for x in ex.istekler if x[0] == 'YOK/USDT') == 1


def test_eksik_aralik_hesabi():
    assert vs.MumDeposu.eksikler([(10, 20), (30, 40)], 0, 50) == [(0, 10), (20, 30), (40, 50)]
    assert vs.MumDeposu.eksikler([(0, 50)], 10, 20) == []
    assert vs.MumDeposu.birlestir([(30, 40), (0, 10), (10, 20)]) == [(0, 20), (30, 40)]


def test_giris_zamani_sure_uzatmasi_duzeltmesi(tmp_path):
    m1 = _m1_dizi(2000)
    gercek_i = 600
    fiyat = m1[gercek_i, 4]
    m1[gercek_i + 240:, 1:5] *= 1.05                        # 4 saat sonra fiyat %5 yukarıda (uzatma anı)
    d = vs.MumDeposu(SayanBorsa({('A/USDT', '1m'): m1}), None, simdi_ms=T0 + 10 ** 9)
    tahmini = T0 + (gercek_i + 240) * 60_000 + 20_000       # CSV: giriş zamanı uzatmada sıfırlanmış
    t, k = vs.giris_bul(d, 'A/USDT', tahmini, fiyat)
    assert k == 1 and abs(t - (T0 + gercek_i * 60_000)) < 60_000
    assert vs.giris_bul(d, 'A/USDT', tahmini, fiyat * 1.2) is None            # hiçbir yerde eşleşmez


# --- uçtan uca ------------------------------------------------------------------------------
from test_labeler_backfill import SahteBorsa, seri_uret, T0 as T0_BF  # noqa: E402


class TickerBorsa(SahteBorsa):
    def fetch_ohlcv(self, sym, tf, since=None, limit=1000):
        if (sym, tf) not in self.veri:
            raise ccxt.BadSymbol(sym)
        return super().fetch_ohlcv(sym, tf, since, limit)


@pytest.fixture(scope='module')
def dunya():
    n = 60 * 24 * 14
    pompalar = list(range(60 * 24 * 6, n - 900, 397))
    return TickerBorsa({'BTC/USDT': seri_uret(1, n, 60000, trend=0.00003),
                        'AAA/USDT': seri_uret(2, n, 1.0, pompa_dk=pompalar),
                        'BBB/USDT': seri_uret(3, n, 50.0, pompa_dk=pompalar[::2])})


def _gercek_csv(yol, borsa):
    """Sentetik dünyada gerçek dolum fiyatlarıyla V1 işlem satırları (biri süre uzatmalı)."""
    d = borsa.veri[('AAA/USDT', '1m')]
    satirlar = []
    for i, dk in enumerate(range(60 * 24 * 7, 60 * 24 * 12, 311)):
        g = float(d['c'].iloc[dk])
        cikis_dk = dk + 90
        c = float(d['c'].iloc[cikis_dk])
        sure = 1.5
        if i == 3:                                          # uzatmalı: Sure_Saat uzatmadan itibaren
            cikis_dk, sure = dk + 300, 1.0
            c = float(d['c'].iloc[cikis_dk])
        t = pd.Timestamp(T0_BF + cikis_dk * 60_000 + 5_000, unit='ms')
        g_net = (c * 0.999 - g * 1.001) / (g * 1.001)
        satirlar.append([str(t), 'AAA/USDT', 'MSB', 'NORMAL', 70.0, 3.0, 1.2, g, c, g_net * 100, g_net * 20,
                         '📈 TREND TAKİPLİ ÇIKIŞ' if c > g else '🛑 STOP LOSS (%-2.5)', sure])
    satirlar.append([str(pd.Timestamp(T0_BF + 60 * 24 * 8 * 60_000, unit='ms')), 'ZZZ/USDT', 'MSB', 'NORMAL', 70.0,
                     3.0, 1.2, 1.0, 1.01, 0.8, 0.16, '📈 TREND TAKİPLİ ÇIKIŞ', 1.0])   # delist: veri yok
    with open(yol, 'w', encoding='utf-8') as f:
        f.write(','.join(vs.gs.V1) + '\n')
        for r in satirlar:
            f.write(','.join(str(x) for x in r) + '\n')
    return len(satirlar)


def test_uctan_uca_gercek_ve_backfill(dunya, tmp_path, monkeypatch):
    monkeypatch.setattr(vs, 'ESKI_SURUM_BITIS', '2100-01-01')   # sentetik tarihler Eylül 2026'ya düşüyor
    csv_yol = tmp_path / 'core_islem_verileri.csv'
    n = _gercek_csv(csv_yol, dunya)
    a = vs.arguman_ayristirici().parse_args([
        str(csv_yol), '--baslangic', str(pd.Timestamp(T0_BF + 6 * 86_400_000, unit='ms')),
        '--onbellek', str(tmp_path / 'onb'), '--cikti', str(tmp_path / 'sim'), '--ai-model', 'yok',
        '--evren', '2', '--butce', '100', '450'])
    simdi = T0_BF + 14 * 86_400_000
    sonuc = vs.calistir(dunya, a, simdi_ms=simdi, log=lambda *x: None)
    g = sonuc['meta']['gercek']
    assert g['pozisyon'] == n and g['veri_yok'] == 1 and g['dogrulanan'] == n - 1
    assert g['uzatma_duzeltilen'] == 1
    assert set(sonuc['senaryolar']) >= {'GERCEK', 'ESKI_SIM', 'V184_CIKIS', 'V184', 'B_V184', 'B_REJIMSIZ'}
    assert len(sonuc['senaryolar']['GERCEK']) == n - 1
    assert len(sonuc['senaryolar']['B_REJIMSIZ']) >= len(sonuc['senaryolar']['B_V184']) >= 1
    assert sonuc['dogrulama']['n'] == n - 1
    for yol in ('sim_rapor.txt', 'sim_rapor.json', 'sim_pozisyonlar.csv', 'sim_backfill.csv'):
        assert (tmp_path / yol).exists(), yol
    rapor = json.loads((tmp_path / 'sim_rapor.json').read_text(encoding='utf-8'))
    assert {r['senaryo'] for r in rapor['butce']} >= {'GERCEK', 'V184', 'B_V184', 'B_V1802'}
    metin = (tmp_path / 'sim_rapor.txt').read_text(encoding='utf-8')
    assert 'SİMÜLATÖR DOĞRULAMASI' in metin and 'PİYASA TARAMASI' in metin
    # AI modeli yok: AI'lı backfill senaryoları yok, Ağustos botu AI'sız çalışır ve kendi çıkışını kullanır
    assert not set(sonuc['senaryolar']) & set(vs.B_AI_SENARYOLARI)
    bt = pd.read_csv(tmp_path / 'sim_backfill.csv')
    assert {'btc_ok', 'btc_ok_eski', 'V184_getiri', 'ESKI_getiri'} <= set(bt.columns)
    assert len(sonuc['senaryolar']['B_V1802']) == int((bt['btc_ok_eski'].astype(bool) & bt['ESKI_getiri'].notna()).sum())
    assert rapor['backfill_karsilastirma']['senaryolar']['B_V184']['n'] == len(sonuc['senaryolar']['B_V184'])
    poz = pd.read_csv(tmp_path / 'sim_pozisyonlar.csv')
    assert not any(c.startswith('_') for c in poz.columns)
    # önbellekten ikinci çalıştırma: yeni API isteği yok
    sayac = {'n': 0}
    eski = dunya.fetch_ohlcv

    def sayan(sym, *x, **k):
        sayac['n'] += sym != 'ZZZ/USDT'        # delist sembol her çalıştırmada bir kez sorulur
        return eski(sym, *x, **k)
    dunya.fetch_ohlcv = sayan
    try:
        vs.calistir(dunya, a, simdi_ms=simdi, log=lambda *x: None)
    finally:
        dunya.fetch_ohlcv = eski
    assert sayac['n'] == 0


def test_gercek_senaryolari_filtreleri_uygular():
    ortak = {'veri': 'VAR', 'kasa_tipi': 'NORMAL', 'giris_fiyat': 100.0, 'giris_dogrulandi': True,
             '_gercek_bacaklar': [(T0 + 60_000, 1.0, 0.01, 'X')],
             '_ESKI_SIM_bacaklar': [(T0 + 60_000, 1.0, 101.0, 'X')], '_V184_bacaklar': [(T0 + 60_000, 1.0, 101.0, 'X')]}
    tablo = pd.DataFrame([
        {**ortak, 'sembol': 'A', 'giris_ms': T0, 'btc_ok': True, 'rejim': 'TREND', 'atr_pct': 2.0, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'B', 'giris_ms': T0, 'btc_ok': True, 'rejim': 'YATAY', 'atr_pct': 2.0, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'C', 'giris_ms': T0, 'btc_ok': False, 'rejim': 'TREND', 'atr_pct': 2.0, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'D', 'giris_ms': T0, 'btc_ok': True, 'rejim': 'TREND', 'atr_pct': 3.5, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'E', 'giris_ms': T0, 'btc_ok': True, 'rejim': 'TREND', 'atr_pct': 2.0, 'ai_skor': 0.2},
        {**ortak, 'sembol': 'F', 'giris_ms': T0, 'btc_ok': True, 'rejim': 'TREND', 'atr_pct': 2.0, 'ai_skor': None},
    ])

    class Yuva:
        esik = 0.65

        def blokla_mi(self, s):
            return s < self.esik
    sen = vs.gercek_senaryolari(tablo, Yuva())
    assert [x['sembol'] for x in sen['V184_CIKIS']] == list('ABCDEF')
    assert [x['sembol'] for x in sen['V184']] == ['A', 'E', 'F']
    assert [x['sembol'] for x in sen['V184_AI']] == ['A']
    assert sen['V184'][0]['bacaklar'][0][2] == pytest.approx(risk.net_oran(100.0, 101.0, FEE))
    assert 'V184_AI' not in vs.gercek_senaryolari(tablo, None)
    # model eğitim verisi girişlerden sonraya uzanıyorsa AI senaryosu o girişleri almaz (örneklem içi olurdu)
    assert vs.gercek_senaryolari(tablo, Yuva(), ai_bas_ms=T0)['V184_AI'] == []
    # giriş zamanı doğrulanamayan pozisyon hiçbir senaryoya girmez
    tablo.loc[0, 'giris_dogrulandi'] = False
    assert [x['sembol'] for x in vs.gercek_senaryolari(tablo, Yuva())['GERCEK']] == list('BCDEF')


def test_komut_satiri_varsayilanlari():
    a = vs.arguman_ayristirici().parse_args([])
    assert a.baslangic == '2026-08-05' and a.kaynak == ['gercek', 'backfill'] and a.butce == [100.0, 450.0]
    assert isinstance(a, argparse.Namespace)


def test_yalniz_veri_yok_pozisyonlarla_cokmez(dunya, tmp_path):
    csv_yol = tmp_path / 'c.csv'
    with open(csv_yol, 'w', encoding='utf-8') as f:
        f.write(','.join(vs.gs.V1) + '\n')
        f.write(','.join(str(x) for x in [str(pd.Timestamp(T0_BF + 8 * 86_400_000, unit='ms')), 'ZZZ/USDT', 'MSB',
                                          'NORMAL', 70, 3, 1.2, 1.0, 1.01, 0.8, 0.16, '📈 TREND TAKİPLİ ÇIKIŞ', 1.0]) + '\n')
    a = vs.arguman_ayristirici().parse_args([str(csv_yol), '--baslangic', str(pd.Timestamp(T0_BF + 6 * 86_400_000, unit='ms')),
                                             '--kaynak', 'gercek', '--ai-model', 'yok', '--onbellek', str(tmp_path / 'o'),
                                             '--cikti', str(tmp_path / 's')])
    sonuc = vs.calistir(dunya, a, simdi_ms=T0_BF + 14 * 86_400_000, log=lambda *x: None)
    assert sonuc['meta']['gercek']['veri_yok'] == 1 and sonuc['senaryolar']['GERCEK'] == []
    assert (tmp_path / 's_rapor.txt').exists()


def test_kartli_yeni_model_ai_senaryosu_guvenli_atlanir(tmp_path):
    import xgboost as xgb
    from sniper.model_karti import modeli_kartla_kaydet
    rng = np.random.default_rng(0)
    X = pd.DataFrame({'Giris_RSI': rng.uniform(50, 90, 200), 'EMA15m_ATR': rng.normal(0, 1, 200)})
    m = xgb.XGBClassifier(n_estimators=5, max_depth=2).fit(X, (X['Giris_RSI'] > 70).astype(int))
    yol = str(tmp_path / 'core_xgboost_model.json')
    modeli_kartla_kaydet(m, yol, {'surum': 'v3-test', 'esik': 0.5, 'ozellikler': list(X.columns),
                                  'veri': {'donem': ['2026-03-01 00:00:00', '2026-09-20 00:00:00']}})
    yuva, son = vs.ai_yukle(yol, log=lambda *x: None)
    assert yuva is not None and son == vs._ms('2026-09-20')
    # Ağustos kayıtlarında EMA15m_ATR yok -> gerçek girişlerde skor canlıdakiyle aynı olamaz
    assert 'EMA15m_ATR' in vs.ai_uygun_mu(yuva, [{'Giris_RSI': 70.0, 'Giris_Vol_Oran': 3.0, 'Giris_ATR_Pct': 1.0}])
    # backfill satırlarında kaynak kolonlar var (EMA15m_ATR = EMA15m_Uzaklik / ATR, botla aynı türetme)
    assert vs.ai_uygun_mu(yuva, [{'Giris_RSI': 70.0, 'EMA15m_Uzaklik': 0.5, 'Giris_ATR_Pct': 1.0}]) is None
    assert vs.ai_yukle('yok') == (None, None)


def test_30_gunluk_pencereler_bitis_gununde_biter():
    islemler = [_islem('A', i * 24 * 60, [(i * 24 * 60 + 60, 1.0, 0.05)]) for i in range(40)]
    islemler.append(_islem('B', 39 * 24 * 60, [(45 * 24 * 60, 1.0, 0.05)]))    # bitişten 6 gün sonra kapanıyor
    r = vs.butce_ozeti(islemler, 1000, 'sabit', 0.2, T0, T0 + 40 * 86_400_000)
    assert r['p30_p10'] == pytest.approx(30.0) and r['p30_medyan'] == pytest.approx(30.0)    # her gün +1 USDT
    assert r['islem'] == 41 and r['toplam_kar'] == pytest.approx(41.0)


# --- V18.0.2 (Ağustos botu) sadakati ---------------------------------------------------------
def v1802_karar(a, tick, m_k, half_sold, gecen_saat, atr_pct, is_w, rsi_83):
    """Kullanıcının Ağustos commit'indeki ai_bot.py (CORE V18.0.2) vip_cuzdan_loop karar mantığı, emirler
    yerine eylem adı döndürecek şekilde satır satır aktarıldı. rsi_83: RSI kontrolü yapılsaydı >= 83 çıkar mıydı."""
    FEE_RATE, ZARAR_ORANI_BALINA, MAX_BEKLEME_SAATI, MIN_BEKLENTI_ORANI = 0.001, 0.020, 4.0, 0.005
    oran = ((tick * (1 - FEE_RATE)) - (a * (1 + FEE_RATE))) / (a * (1 + FEE_RATE))
    if oran > m_k:
        m_k = oran
    dinamik_stop = max(0.025, min(0.055, (atr_pct * 1.5) / 100))
    aktif_zarar_orani = ZARAR_ORANI_BALINA if is_w else dinamik_stop
    if m_k >= 0.025:
        base_stop = 0.002
    else:
        base_stop = -aktif_zarar_orani
    rsi_vurkac_tetiklendi = False
    if oran >= 0.05 and not half_sold:          # (+ 60 sn aralık: testte her çağrı yeni kontrol)
        rsi_vurkac_tetiklendi = rsi_83
    if rsi_vurkac_tetiklendi and not half_sold:
        return 'MOON'
    if m_k >= 0.20:
        kismi, cikis = m_k - 0.05, max(base_stop, m_k - 0.10)
    elif m_k >= 0.10:
        kismi, cikis = m_k - 0.02, max(base_stop, m_k - 0.05)
    elif m_k >= 0.04:
        kismi, cikis = m_k - 0.015, max(base_stop, m_k - 0.03)
    else:
        kismi, cikis = 999.0, (0.005 if half_sold else base_stop)
    if m_k >= 0.04 and oran <= kismi and not half_sold and not rsi_vurkac_tetiklendi:
        return 'KISMI'
    if oran <= cikis:
        if m_k >= 0.04:
            return 'TREND'
        elif half_sold:
            return 'GUVENLI'
        elif m_k >= 0.025:
            return 'BASA_BAS'
        return f"STOP {aktif_zarar_orani * 100:.1f}"
    elif gecen_saat >= MAX_BEKLEME_SAATI and not half_sold:
        return 'ZAMAN' if oran < MIN_BEKLENTI_ORANI else 'UZAT'
    return None


def motor_karari(a, tick, m_k, half_sold, gecen_saat, atr_pct, is_w, rsi_83, ayar):
    simdi = 1_800_000_000.0
    p = risk.Pozisyon(sembol='X', giris=a, max_kar=m_k, half_sold=half_sold, giris_zamani=simdi - gecen_saat * 3600,
                      atr_pct=atr_pct, is_whale=is_w)
    sev = risk.seviyeleri_hesapla(p, tick, ayar)
    rsi_tetik = rsi_83 if risk.rsi_kontrolu_gerekli(p, sev, simdi, ayar) else False
    for e in risk.kararlar(p, sev, simdi, 'TREND', ayar, rsi_tetik):
        if e.tip == risk.MOON_BAG:
            return 'MOON'
        if e.tip == risk.KISMI_KAR:
            return 'KISMI'
        if e.tip == risk.ZAMAN_UZAT:
            return 'UZAT'
        if e.tip == risk.TAM_CIKIS:
            return {risk.MSG_TREND: 'TREND', risk.MSG_GUVENLI: 'GUVENLI', risk.MSG_KILIT: 'BASA_BAS',
                    risk.MSG_ZAMAN: 'ZAMAN'}.get(e.mesaj) or 'STOP ' + e.mesaj.split('%-')[1].rstrip(')')
    return None


def test_eski_ayarlar_v1802_ile_ayni_karari_verir():
    rng = np.random.default_rng(42)
    farkli = []
    for _ in range(20_000):
        a = 1.0
        tick = float(rng.uniform(0.92, 1.35))
        m_k = float(rng.choice([0.0, rng.uniform(0, 0.03), rng.uniform(0.02, 0.06), rng.uniform(0.03, 0.35)]))
        args = (a, tick, m_k, bool(rng.random() < 0.3), float(rng.uniform(0, 8)), float(rng.uniform(0.1, 4.5)),
                bool(rng.random() < 0.2), bool(rng.random() < 0.3))
        beklenen, bulunan = v1802_karar(*args), motor_karari(*args, vs.RISK_ESKI)
        if beklenen != bulunan:
            farkli.append((args, beklenen, bulunan))
    assert not farkli, farkli[:5]
    # V18.4 ayarları V18.0.2'den gerçekten farklı karar verir (testin ayırt edici olduğunu gösterir)
    fark_v184 = sum(v1802_karar(*x) != motor_karari(*x, vs.RISK_V184) for x in [
        (1.0, 1.017, 0.035, False, 1.0, 1.0, False, False),     # m_k %3.5, net %1.5: V18.4'te (ATR 1 -> eşik %3) kısmi kâr
        (1.0, 1.012, 0.028, False, 1.0, 1.0, False, False)])    # kilit: V18.0.2 +%0.2'de tutar, V18.4 +%1'de satar
    assert fark_v184 == 2


def test_v1802_senaryosu_agustos_giris_filtrelerini_uygular():
    ortak = {'veri': 'VAR', 'kasa_tipi': 'NORMAL', 'giris_fiyat': 100.0, 'giris_dogrulandi': True, 'btc_ok': True,
             'rejim': 'TREND', '_gercek_bacaklar': [(T0 + 60_000, 1.0, 0.01, 'X')],
             '_ESKI_SIM_bacaklar': [(T0 + 60_000, 1.0, 101.0, 'X')], '_V184_bacaklar': [(T0 + 60_000, 1.0, 101.0, 'X')]}
    tablo = pd.DataFrame([
        {**ortak, 'sembol': 'A', 'giris_ms': T0, 'btc_ok_eski': True, 'atr_pct': 3.5, 'ai_skor': 0.9},   # ATR<=4 geçer
        {**ortak, 'sembol': 'B', 'giris_ms': T0, 'btc_ok_eski': True, 'atr_pct': 4.2, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'C', 'giris_ms': T0, 'btc_ok_eski': False, 'atr_pct': 2.0, 'ai_skor': 0.9},
        {**ortak, 'sembol': 'D', 'giris_ms': T0, 'btc_ok_eski': True, 'atr_pct': 2.0, 'ai_skor': 0.3},
    ])

    class Eski:
        kart, esik = None, 0.65

        def blokla_mi(self, s):
            return s < self.esik

    class Yeni(Eski):
        kart = {'surum': 'v3'}
    assert [x['sembol'] for x in vs.gercek_senaryolari(tablo, Eski())['V1802']] == ['A']
    assert [x['sembol'] for x in vs.gercek_senaryolari(tablo, None)['V1802']] == ['A', 'D']
    # yeni (kartlı) model Ağustos botunda yoktu: V1802'ye uygulanmaz
    assert [x['sembol'] for x in vs.gercek_senaryolari(tablo, Yeni())['V1802']] == ['A', 'D']


def test_btc_eski_kurali_histerezissiz_ve_cokus_korumali():
    n = 700
    c = np.r_[np.linspace(100, 90, 350), np.linspace(90, 110, 340), [110, 108.0, 111, 111, 111, 111, 111, 111, 111, 111]]
    df = pd.DataFrame({'ts': T0 + np.arange(n) * 900_000, 'o': c, 'h': c * 1.001, 'l': c * 0.999, 'c': c, 'v': 1.0})
    b = vs.BtcBaglami(df)
    kap = lambda i: T0 + i * 900_000 + 900_000  # noqa: E731
    assert b.eski_ok(kap(340)) is False                     # düşüş sürerken EMA200 altında
    assert b.eski_ok(kap(685)) is True                      # EMA200 üstünde
    assert b.eski_ok(kap(691)) is False                     # 110 -> 108: %1.8 ani düşüş
    assert b.eski_ok(kap(699)) is True


def _b_satir(sembol, atr, rejim, btc_ok, btc_ok_eski, skor, v184=True, eski=True):
    b = [(T0 + 60_000, 1.0, 101.0, 'V')]
    e = [(T0 + 60_000, 1.0, 102.0, 'E')]
    return {'sembol': sembol, 'giris_ms': T0, 'giris_fiyat': 100.0, 'kasa_tipi': 'NORMAL', 'degisim_24s': 0.0,
            'atr_pct': atr, 'rejim': rejim, 'btc_ok': btc_ok, 'btc_ok_eski': btc_ok_eski, 'ai_skor': skor,
            '_V184_bacaklar': b if v184 else float('nan'), '_ESKI_bacaklar': e if eski else float('nan')}


def test_backfill_senaryolari_ayni_havuzu_suzer():
    """Altı senaryo aynı sinyal havuzunu süzer: V18.4 (ATR %3, TREND, histerezisli BTC), ATR %4 ve yatay rejim
    varyantları, Ağustos botu (histerezissiz BTC, ATR %4, eski AI, V18.0.2 çıkışı)."""
    tablo = pd.DataFrame([
        _b_satir('A', 2.0, 'TREND', True, True, 0.9),     # her yerde
        _b_satir('B', 3.5, 'TREND', True, True, 0.9),     # ATR %3-4: yalnız ATR4 ve Ağustos
        _b_satir('C', 2.0, 'YATAY', True, True, 0.9),     # yatay: rejimsizler ve Ağustos
        _b_satir('D', 2.0, 'TREND', True, True, 0.3),     # AI reddi
        _b_satir('E', 2.0, 'TREND', False, True, 0.9, v184=False),   # yalnız eski BTC kuralı onaylı
        _b_satir('F', 2.0, 'TREND', True, False, 0.9, eski=False),   # yalnız V18.4 BTC kuralı onaylı
        _b_satir('G', 3.9, 'YATAY', True, True, 0.9),     # ATR4 yatay: ATR4'e girmez (TREND şartı), Ağustos'a girer
    ])

    class Eski:
        kart, esik = None, 0.65

        def blokla_mi(self, s):
            return s < self.esik

    class Yeni(Eski):
        kart = {'surum': 'v3'}
    sen = vs.backfill_senaryolari(tablo, Eski())
    ad = lambda k: [x['sembol'] for x in sen[k]]  # noqa: E731
    assert ad('B_REJIMSIZ') == ['A', 'C', 'D', 'F']
    assert ad('B_V184') == ['A', 'D', 'F']
    assert ad('B_V184_AI') == ['A', 'F']
    assert ad('B_AI_ATR4') == ['A', 'B', 'F']
    assert ad('B_AI_REJIMSIZ') == ['A', 'C', 'F']
    assert ad('B_V1802') == ['A', 'B', 'C', 'E', 'G']
    # V18.4 senaryoları V18.4 çıkışını, Ağustos botu V18.0.2 çıkışını kullanır
    assert sen['B_V184'][0]['bacaklar'][0][2] == pytest.approx(risk.net_oran(100.0, 101.0, FEE))
    assert sen['B_V1802'][0]['bacaklar'][0][2] == pytest.approx(risk.net_oran(100.0, 102.0, FEE))
    # AI yoksa AI'lı senaryolar yok, Ağustos botu AI'sız; yeni (kartlı) model Ağustos botuna uygulanmaz
    yok = vs.backfill_senaryolari(tablo, None)
    assert not set(yok) & set(vs.B_AI_SENARYOLARI)
    assert [x['sembol'] for x in yok['B_V1802']] == ['A', 'B', 'C', 'D', 'E', 'G']
    assert [x['sembol'] for x in vs.backfill_senaryolari(tablo, Yeni())['B_V1802']] == ['A', 'B', 'C', 'D', 'E', 'G']


def test_aylik_bolum_ay_sonu_bakiye_ve_senaryo_tablosu():
    def satir(ad, butce, aylik, kar, n, mod='sabit'):
        return {'senaryo': ad, 'butce': butce, 'mod': mod, 'islem': n, 'toplam_kar': kar,
                'getiri_pct': kar / butce * 100, 'max_dusus_pct': -2.0, 'aylik': aylik}
    ocak_ai = {'islem': 1, 'kar_usdt': -0.5, 'kazanma': 0.0}
    mart = {'islem': 3, 'kar_usdt': 2.0, 'kazanma': 2 / 3}
    tablo = [satir('B_V184', 450, {'2026-01': {'islem': 2, 'kar_usdt': 1.0, 'kazanma': 0.5}}, 1.0, 2),
             satir('B_V184_AI', 450, {'2026-01': ocak_ai, '2026-03': mart}, 1.5, 4),
             satir('B_V184_AI', 100, {'2026-03': mart}, 2.0, 3),
             satir('B_V184_AI', 450, {'2026-01': {'islem': 9, 'kar_usdt': 9.0, 'kazanma': 1.0}}, 9.0, 9, mod='oransal')]
    y = vs.aylik_bolum(tablo)
    assert 'AY AY SONUÇ (sabit kasa' in y[1]           # canlı bot gibi sabit kasa tercih edilir
    bas = y.index(next(s for s in y if '450 USDT ile' in s))
    blok = [s.split() for s in y[bas + 2:bas + 6]]
    # Ocak -0.50 -> 449.50; Şubat işlemsiz (yine de görünür) 449.50; Mart +2.00 -> 451.50; toplamda %50 kazanan
    assert blok[0] == ['2026-01', '1', '-0.50', '0%', '449.50']
    assert blok[1] == ['2026-02', '0', '+0.00', '-', '449.50']
    assert blok[2] == ['2026-03', '3', '+2.00', '67%', '451.50']
    assert blok[3][:5] == ['TOPLAM', '4', '+1.50', '50%', '451.50']
    assert any('100 USDT ile' in s for s in y) and y.index(next(s for s in y if '100 USDT ile' in s)) > bas
    mat = y[y.index(next(s for s in y if 'Tüm senaryolar' in s)):]
    assert mat[1].split() == ['ay', 'B_V184', 'B_V184_AI'] and '450 USDT bütçe' in mat[0]
    assert mat[2].split() == ['2026-01', '+1.00', '-0.50'] and mat[4].split() == ['2026-03', '+0.00', '+2.00']
    assert mat[5].split() == ['TOPLAM', '+1.00', '+1.50'] and mat[6].split() == ['işlem', '2', '4']
    assert vs.aylik_bolum([]) == [] and vs.aylik_bolum([satir('B_V184', 100, {}, 0.0, 0)]) == []


def test_backfill_karsilastirmasi_eklenen_sinyalleri_ayri_olcer():
    def islem(sembol, gun, getiri):
        return {'sembol': sembol, 'giris_ms': T0 + gun * vs.GUN_MS, 'kasa_tipi': 'NORMAL',
                'bacaklar': [(0, 0.5, getiri), (1, 0.5, getiri)], 'son_mesaj': 'X'}
    taban = [islem(f"T{i}", i, 0.01) for i in range(20)]
    atr = [islem(f"A{i}", i, -0.02) for i in range(8)]
    yatay = [islem(f"Y{i}", i, 0.004) for i in range(6)]
    ozet = vs.backfill_karsilastirma({'B_V184_AI': taban, 'B_AI_ATR4': taban + atr, 'B_AI_REJIMSIZ': taban + yatay,
                                      'B_V1802': [islem(f"E{i}", i, 0.02) for i in range(15)], 'V184': taban})
    assert set(ozet['senaryolar']) == {'B_V184_AI', 'B_AI_ATR4', 'B_AI_REJIMSIZ', 'B_V1802'}   # yalnız backfill
    assert ozet['senaryolar']['B_V184_AI'] == pytest.approx(
        {'n': 20, 'ort': 0.01, 'kazanan': 1.0, 'ga': [pytest.approx(0.01), pytest.approx(0.01)]})
    assert ozet['eklenen']['atr_3_4']['n'] == 8 and ozet['eklenen']['atr_3_4']['ort'] == pytest.approx(-0.02)
    assert ozet['eklenen']['yatay_rejim']['ort'] == pytest.approx(0.004)
    f = ozet['fark']['B_AI_ATR4']
    assert f['ort'] == pytest.approx((20 * 0.01 - 8 * 0.02) / 28 - 0.01) and f['ga'][1] < 0
    assert ozet['fark']['B_V1802']['ort'] == pytest.approx(0.01) and ozet['fark']['B_V1802']['ga'][0] > 0
    assert vs.backfill_karsilastirma({'GERCEK': taban}) == {}


def test_uctan_uca_backfill_eski_kartsiz_modelle(dunya, tmp_path):
    """Sunucudaki durum: kartsız eski core model. Altı backfill senaryosu da üretilir; Ağustos botu yalnız eski
    modelin geçirdiği sinyallere girer, AI'lı V18.4 varyantları B_V184_AI'yı kapsar."""
    import xgboost as xgb
    rng = np.random.default_rng(1)
    X = pd.DataFrame({'Giris_RSI': rng.uniform(55, 90, 400), 'Giris_Vol_Oran': rng.uniform(2.5, 9, 400),
                      'Giris_ATR_Pct': rng.uniform(0.3, 4.0, 400), 'Sinyal_Encoded': 1.0})
    m = xgb.XGBClassifier(n_estimators=20, max_depth=2).fit(X, (X['Giris_Vol_Oran'] > 4.5).astype(int))
    model = tmp_path / 'core_xgboost_model.json'
    m.save_model(str(model))
    a = vs.arguman_ayristirici().parse_args([
        '--baslangic', str(pd.Timestamp(T0_BF + 6 * 86_400_000, unit='ms')), '--kaynak', 'backfill',
        '--onbellek', str(tmp_path / 'onb'), '--cikti', str(tmp_path / 'sim'), '--ai-model', str(model),
        '--evren', '2', '--butce', '100'])
    sonuc = vs.calistir(dunya, a, simdi_ms=T0_BF + 14 * 86_400_000, log=lambda *x: None)
    sen = sonuc['senaryolar']
    assert set(sen) == set(vs.B_SENARYOLAR)
    k = lambda ad: {(x['sembol'], x['giris_ms']) for x in sen[ad]}  # noqa: E731
    assert k('B_V184_AI') <= k('B_V184') and k('B_V184_AI') <= k('B_AI_ATR4') and k('B_V184_AI') <= k('B_AI_REJIMSIZ')
    bt = pd.read_csv(tmp_path / 'sim_backfill.csv')
    skor = {(r.sembol, int(r.giris_ms)): r.ai_skor for r in bt.itertuples()}
    assert sen['B_V1802'] and all(skor[x] >= 0.65 for x in k('B_V1802'))
    assert any(s < 0.65 for s in skor.values())                  # model gerçekten bazı sinyalleri eliyor
    metin = (tmp_path / 'sim_rapor.txt').read_text(encoding='utf-8')
    assert '(taban)' in metin and 'B_V1802' in metin
    assert 'AY AY SONUÇ (sabit kasa' in metin and 'B_V184_AI: backfill, V18.4 + AI (canlıdaki kurulum); 100 USDT ile' in metin
    assert any('delist' in n for n in sonuc['meta']['notlar'])
    a4 = vs.arguman_ayristirici().parse_args([
        '--baslangic', str(pd.Timestamp(T0_BF + 6 * 86_400_000, unit='ms')), '--kaynak', 'backfill',
        '--onbellek', str(tmp_path / 'onb'), '--cikti', str(tmp_path / 'r4'), '--ai-model', str(model),
        '--evren', '2', '--butce', '100', '--radar-saat', '4'])
    s4 = vs.calistir(dunya, a4, simdi_ms=T0_BF + 14 * 86_400_000, log=lambda *x: None)
    assert any('DENEY: radar son 4 saatte' in n for n in s4['meta']['notlar'])
    assert (tmp_path / 'r4_rapor.txt').exists()
